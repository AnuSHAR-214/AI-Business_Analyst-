#!/usr/bin/env python3
"""
Export pre-aggregated cubes for the standalone HTML demo.

The web demo has no Python and no server, so it can't run SQL. Instead we ship
small aggregate cubes (month x one dimension) and let JavaScript do the same
contribution-to-change maths the Python analyzer does. The numbers are
therefore identical to the real app's - they come from the same database.

Documented limitation: because the cubes are one-dimensional, the web demo can
break down by any single dimension but cannot cross-filter (e.g. "Electronics
in EMEA"). The full Python app has no such limit.

    python data/export_web.py   ->  docs/web_data.json
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = os.environ.get("RETAIL_DB_PATH", str(ROOT / "data" / "retail.db"))
OUT = ROOT / "docs" / "web_data.json"

DIMS = {
    "category": "category",
    "region": "region_name",
    "channel": "channel",
    "segment": "segment",
    "product": "product_name",
    "customer": "customer_name",
}

# customers/products are long tails - keep the meaningful head
TOP_N = {"customer": 80, "product": 25}


def cube(con: sqlite3.Connection, dim: str, col: str) -> dict:
    limit = TOP_N.get(dim)
    if limit:
        keep = [r[0] for r in con.execute(
            f"SELECT {col} FROM v_sales GROUP BY 1 "
            f"ORDER BY SUM(revenue) DESC LIMIT {limit}")]
        placeholders = ",".join("?" * len(keep))
        rows = con.execute(
            f"SELECT order_month, {col}, ROUND(SUM(revenue),2), "
            f"ROUND(SUM(profit),2), COUNT(DISTINCT order_id), SUM(quantity) "
            f"FROM v_sales WHERE {col} IN ({placeholders}) "
            f"GROUP BY 1,2 ORDER BY 1,2", keep).fetchall()
    else:
        rows = con.execute(
            f"SELECT order_month, {col}, ROUND(SUM(revenue),2), "
            f"ROUND(SUM(profit),2), COUNT(DISTINCT order_id), SUM(quantity) "
            f"FROM v_sales GROUP BY 1,2 ORDER BY 1,2").fetchall()

    labels = sorted({r[1] for r in rows})
    idx = {label: i for i, label in enumerate(labels)}
    # compact: [monthIndex, labelIndex, revenue, profit, orders, units]
    return {"labels": labels, "rows": rows, "idx": idx}


def main() -> None:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

    months = [r[0] for r in con.execute(
        "SELECT DISTINCT order_month FROM v_sales ORDER BY 1")]
    month_idx = {m: i for i, m in enumerate(months)}

    data: dict = {
        "generated_from": os.path.basename(DB),
        "months": months,
        "totals": [],
        "cubes": {},
        "meta": {},
    }

    for m, rev, prof, orders, units in con.execute(
        "SELECT order_month, ROUND(SUM(revenue),2), ROUND(SUM(profit),2), "
        "COUNT(DISTINCT order_id), SUM(quantity) FROM v_sales GROUP BY 1 ORDER BY 1"
    ):
        data["totals"].append([rev, prof, orders, units])

    for dim, col in DIMS.items():
        c = cube(con, dim, col)
        packed = [[month_idx[r[0]], c["idx"][r[1]], r[2], r[3], r[4], r[5]]
                  for r in c["rows"] if r[0] in month_idx]
        data["cubes"][dim] = {"labels": c["labels"], "rows": packed}

    # cancellation rate by month - powers the sanity-check caveat
    data["cancel_rate"] = [
        [m, rate] for m, rate in con.execute(
            "SELECT substr(order_date,1,7), "
            "ROUND(100.0*SUM(status='Cancelled')/COUNT(*),2) "
            "FROM orders GROUP BY 1 ORDER BY 1")
    ]

    data["meta"] = {
        "orders": con.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
        "line_items": con.execute("SELECT COUNT(*) FROM order_items").fetchone()[0],
        "customers": con.execute("SELECT COUNT(*) FROM customers").fetchone()[0],
        "products": con.execute("SELECT COUNT(*) FROM products").fetchone()[0],
        "date_range": list(con.execute(
            "SELECT MIN(order_date), MAX(order_date) FROM orders").fetchone()),
        "top_n": TOP_N,
    }
    con.close()

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    size = OUT.stat().st_size / 1024
    print(f"Wrote {OUT}  ({size:.0f} KB)")
    print(f"  months={len(months)}  " + "  ".join(
        f"{d}={len(v['rows'])}rows/{len(v['labels'])}vals"
        for d, v in data["cubes"].items()))


if __name__ == "__main__":
    main()
