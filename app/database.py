"""Database access + schema introspection.

The schema description produced here is what gets injected into the LLM prompt,
so it is written for a reader, not a machine: table purpose, column meanings and
a few real example values for low-cardinality columns. Giving the model example
values ("Electronics", "EMEA", "Enterprise") is the single biggest accuracy win
in text-to-SQL - it stops the model inventing category names.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .config import settings
from .guard import enforce_limit, validate_sql

TABLE_NOTES = {
    "regions": "Sales regions. One row per region.",
    "customers": "Customers. segment is Enterprise / SMB / Consumer.",
    "products": "Product catalogue with unit_cost and list unit_price.",
    "orders": "Order headers. status is 'Completed' or 'Cancelled'.",
    "order_items": "Order line items. revenue = quantity * unit_price (after discount).",
    "v_sales": (
        "PREFERRED denormalised view joining orders + items + customers + "
        "products + regions, already filtered to status='Completed'. "
        "Use this view for almost every question."
    ),
}


@dataclass
class QueryResult:
    sql: str
    dataframe: pd.DataFrame
    row_count: int
    truncated: bool


class Database:
    def __init__(self, path: str | None = None) -> None:
        self.path = str(path or settings.db_path)
        if not Path(self.path).exists():
            raise FileNotFoundError(
                f"Database not found at {self.path}. "
                "Run:  python data/generate_data.py"
            )

    # -- connection --------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        # read-only URI connection: defence in depth behind the SQL guard
        con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        return con

    # -- introspection -----------------------------------------------------
    def schema_description(self, sample_values: bool = True) -> str:
        lines: list[str] = []
        with self._connect() as con:
            objects = con.execute(
                "SELECT name, type FROM sqlite_master "
                "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' "
                "ORDER BY CASE WHEN name='v_sales' THEN 0 ELSE 1 END, name"
            ).fetchall()

            for obj in objects:
                name = obj["name"]
                note = TABLE_NOTES.get(name, "")
                lines.append(f"\n{obj['type'].upper()} {name}  -- {note}")
                cols = con.execute(f"PRAGMA table_info({name})").fetchall()
                for col in cols:
                    entry = f"  - {col['name']} ({col['type'] or 'TEXT'})"
                    if sample_values and (col["type"] or "TEXT").upper() in ("TEXT",):
                        vals = con.execute(
                            f"SELECT DISTINCT {col['name']} FROM {name} "
                            f"WHERE {col['name']} IS NOT NULL LIMIT 8"
                        ).fetchall()
                        distinct = [str(v[0]) for v in vals]
                        if 0 < len(distinct) <= 6:
                            entry += "  e.g. " + ", ".join(repr(d) for d in distinct)
                    lines.append(entry)

            rng = con.execute(
                "SELECT MIN(order_date), MAX(order_date) FROM orders"
            ).fetchone()
        lines.append(f"\nData covers order_date from {rng[0]} to {rng[1]}.")
        lines.append("Dates are TEXT in 'YYYY-MM-DD' format; use "
                     "substr(order_date,1,7) for month and strftime() for parts.")
        return "\n".join(lines).strip()

    def date_range(self) -> tuple[str, str]:
        with self._connect() as con:
            row = con.execute(
                "SELECT MIN(order_date), MAX(order_date) FROM orders"
            ).fetchone()
        return row[0], row[1]

    def distinct_values(self, column: str, table: str = "v_sales") -> list[str]:
        with self._connect() as con:
            rows = con.execute(
                f"SELECT DISTINCT {column} FROM {table} "
                f"WHERE {column} IS NOT NULL ORDER BY 1"
            ).fetchall()
        return [str(r[0]) for r in rows]

    # -- execution ---------------------------------------------------------
    def run(self, sql: str, max_rows: int | None = None) -> QueryResult:
        max_rows = max_rows or settings.max_rows
        safe = validate_sql(sql)
        limited = enforce_limit(safe, max_rows + 1)
        with self._connect() as con:
            df = pd.read_sql_query(limited, con)
        truncated = len(df) > max_rows
        if truncated:
            df = df.head(max_rows)
        return QueryResult(sql=safe, dataframe=df, row_count=len(df),
                           truncated=truncated)
