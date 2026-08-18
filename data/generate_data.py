"""
Generate a realistic retail-analytics SQLite database.

The data is deliberately engineered so that the flagship demo question
    "Why did revenue drop in March?"
has a real, discoverable answer in the data:

  * Total revenue in Mar-2026 falls ~18% vs Feb-2026
  * Electronics is the worst-hit category (~-25%)
  * Two large enterprise accounts cut their order volume sharply
  * The drop is concentrated in the EMEA region and the Online channel

Nothing about the drop is hard-coded into the app - the analyst engine has
to find it by querying the database.

Run:  python data/generate_data.py
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date, timedelta

import numpy as np

RNG = np.random.default_rng(20260315)

DB_PATH = os.environ.get(
    "RETAIL_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "retail.db"),
)

START = date(2024, 1, 1)
END = date(2026, 6, 30)

# --- the "shock" we bury in the data -------------------------------------
SHOCK_MONTH = (2026, 3)
SHOCK_GLOBAL = 0.873          # every order line in March is dampened
SHOCK_CATEGORY = {            # multiplied on top of the global factor
    "Electronics": 0.862,
    "Home & Kitchen": 0.97,
}
SHOCK_ACCOUNTS = 0.30         # the two churn-risk accounts almost stop buying
SHOCK_REGION = {"EMEA": 0.82}
SHOCK_CHANNEL = {"Online": 0.90}

# -------------------------------------------------------------------------
REGIONS = [
    (1, "North America", "United States"),
    (2, "EMEA", "Germany"),
    (3, "APAC", "Singapore"),
    (4, "LATAM", "Brazil"),
]

CATEGORIES = {
    # category: (weight, [(product, cost, price), ...])
    "Electronics": (
        0.34,
        [
            ("Aurora 14in Laptop", 640, 1149),
            ("Aurora 16in Laptop Pro", 980, 1799),
            ("Nimbus Wireless Earbuds", 28, 89),
            ("Nimbus Over-Ear Headphones", 74, 199),
            ("Vertex 27in 4K Monitor", 185, 379),
            ("Vertex Mechanical Keyboard", 42, 119),
            ("Pulse Smartwatch S3", 96, 249),
            ("Pulse Fitness Band", 21, 69),
        ],
    ),
    "Home & Kitchen": (
        0.24,
        [
            ("Hearth Espresso Machine", 118, 289),
            ("Hearth Air Fryer XL", 62, 149),
            ("Loom Cotton Bedding Set", 34, 99),
            ("Loom Weighted Blanket", 41, 119),
            ("Verdant Robot Vacuum", 154, 349),
        ],
    ),
    "Office": (
        0.18,
        [
            ("Meridian Standing Desk", 210, 449),
            ("Meridian Ergonomic Chair", 168, 379),
            ("Clarity Desk Lamp", 19, 59),
            ("Clarity Monitor Arm", 33, 89),
        ],
    ),
    "Apparel": (
        0.14,
        [
            ("Trail Merino Base Layer", 24, 79),
            ("Trail Rain Shell", 48, 159),
            ("Urban Everyday Sneaker", 37, 109),
        ],
    ),
    "Sports & Outdoors": (
        0.10,
        [
            ("Summit Trekking Poles", 26, 79),
            ("Summit 45L Backpack", 55, 149),
            ("Current Yoga Mat Pro", 17, 55),
        ],
    ),
}

SEGMENTS = ["Enterprise", "SMB", "Consumer"]
CHANNELS = ["Online", "Retail Store", "Partner", "Marketplace"]

COMPANY_PREFIX = [
    "Northwind", "Blue Harbor", "Cedar Point", "Ironclad", "Silverline",
    "Bright Path", "Kestrel", "Lantern", "Copper Ridge", "Halcyon",
    "Granite", "Fairmont", "Willow Creek", "Onyx", "Redwood",
    "Tidewater", "Beacon", "Juniper", "Meridian", "Quarry",
]
COMPANY_SUFFIX = ["Group", "Holdings", "Industries", "Labs", "Partners",
                  "Retail", "Logistics", "Systems", "Trading", "Supply Co"]

FIRST = ["Ana", "Marcus", "Priya", "Tomas", "Wei", "Sofia", "Daniel", "Leila",
         "Jonas", "Hana", "Owen", "Ines", "Rahul", "Clara", "Mateo", "Yuki",
         "Noor", "Felix", "Amara", "Diego"]
LAST = ["Silva", "Brenner", "Nair", "Kowalski", "Chen", "Rossi", "Okafor",
        "Haddad", "Lindqvist", "Sato", "Fletcher", "Moreau", "Iyer", "Novak",
        "Alvarez", "Tanaka", "Rahman", "Weber", "Mensah", "Castillo"]


def month_key(d: date) -> tuple[int, int]:
    return (d.year, d.month)


def build_products() -> list[tuple]:
    rows, pid = [], 1
    for cat, (_, items) in CATEGORIES.items():
        for name, cost, price in items:
            rows.append((pid, name, cat, float(cost), float(price)))
            pid += 1
    return rows


def build_customers(n_business: int = 60, n_consumer: int = 240) -> list[tuple]:
    rows = []
    cid = 1
    used = set()
    for _ in range(n_business):
        while True:
            name = f"{RNG.choice(COMPANY_PREFIX)} {RNG.choice(COMPANY_SUFFIX)}"
            if name not in used:
                used.add(name)
                break
        segment = "Enterprise" if RNG.random() < 0.45 else "SMB"
        region = int(RNG.choice([1, 2, 3, 4], p=[0.42, 0.28, 0.20, 0.10]))
        signup = START - timedelta(days=int(RNG.integers(30, 900)))
        rows.append((cid, name, segment, region, signup.isoformat()))
        cid += 1
    for _ in range(n_consumer):
        name = f"{RNG.choice(FIRST)} {RNG.choice(LAST)}"
        region = int(RNG.choice([1, 2, 3, 4], p=[0.40, 0.27, 0.21, 0.12]))
        signup = START + timedelta(days=int(RNG.integers(-500, 800)))
        rows.append((cid, name, "Consumer", region, signup.isoformat()))
        cid += 1
    return rows


def customer_weights(customers: list[tuple]) -> np.ndarray:
    """Enterprise accounts buy far more often than consumers."""
    base = {"Enterprise": 6.0, "SMB": 2.2, "Consumer": 1.0}
    w = np.array([base[c[2]] for c in customers], dtype=float)
    w *= RNG.uniform(0.6, 1.6, size=len(w))
    return w / w.sum()


def seasonality(d: date) -> float:
    """Yearly seasonality: Q4 peak, Feb/Mar trough, summer dip."""
    doy = d.timetuple().tm_yday
    wave = 1.0 + 0.16 * np.sin(2 * np.pi * (doy - 60) / 365.0)
    holiday = 1.0
    if d.month == 11:
        holiday = 1.28
    elif d.month == 12:
        holiday = 1.34
    elif d.month == 1:
        holiday = 0.88
    return float(wave * holiday)


def weekday_factor(d: date) -> float:
    return [1.05, 1.04, 1.02, 1.03, 1.00, 0.82, 0.74][d.weekday()]


def trend(d: date) -> float:
    """~14% annual organic growth."""
    days = (d - START).days
    return float(1.0 + 0.14 * days / 365.0)


def main() -> None:
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    products = build_products()
    customers = build_customers()
    cust_w = customer_weights(customers)

    # pick the two "at risk" enterprise accounts (largest weights)
    ent_idx = [i for i, c in enumerate(customers) if c[2] == "Enterprise"]
    at_risk = sorted(ent_idx, key=lambda i: -cust_w[i])[:2]
    at_risk_ids = {customers[i][0] for i in at_risk}

    prod_cat = {p[0]: p[2] for p in products}
    prod_price = {p[0]: p[4] for p in products}
    prod_cost = {p[0]: p[3] for p in products}
    cust_region = {c[0]: c[3] for c in customers}
    region_name = {r[0]: r[1] for r in REGIONS}

    cat_names = list(CATEGORIES.keys())
    cat_weights = np.array([CATEGORIES[c][0] for c in cat_names])
    cat_weights = cat_weights / cat_weights.sum()
    cat_products = {c: [p[0] for p in products if p[2] == c] for c in cat_names}

    orders, items = [], []
    order_id, item_id = 1, 1
    day = START
    while day <= END:
        shock = month_key(day) == SHOCK_MONTH
        base = 26.0 * trend(day) * seasonality(day) * weekday_factor(day)
        if shock:
            base *= SHOCK_GLOBAL
        n_orders = int(RNG.poisson(base))

        for _ in range(n_orders):
            ci = int(RNG.choice(len(customers), p=cust_w))
            cust = customers[ci]
            cust_id = cust[0]
            region = region_name[cust_region[cust_id]]
            channel = str(RNG.choice(CHANNELS, p=[0.46, 0.24, 0.16, 0.14]))

            if shock:
                keep = 1.0
                if cust_id in at_risk_ids:
                    keep *= SHOCK_ACCOUNTS
                keep *= SHOCK_REGION.get(region, 1.0)
                keep *= SHOCK_CHANNEL.get(channel, 1.0)
                if RNG.random() > keep:
                    continue  # this order never happens in March

            status = "Cancelled" if RNG.random() < (0.045 if shock else 0.025) else "Completed"
            orders.append((order_id, cust_id, day.isoformat(), channel, status))

            n_items = int(RNG.integers(1, 5 if cust[2] != "Consumer" else 3))
            for _ in range(n_items):
                cat = str(RNG.choice(cat_names, p=cat_weights))
                if shock and RNG.random() > SHOCK_CATEGORY.get(cat, 1.0):
                    continue
                pid = int(RNG.choice(cat_products[cat]))
                qty = int(RNG.integers(1, 6 if cust[2] == "Enterprise" else 3))
                list_price = prod_price[pid]
                discount = float(np.round(RNG.choice([0, 0, 0, 0.05, 0.10, 0.15, 0.20],
                                                     p=[.42, .12, .10, .14, .12, .06, .04]), 2))
                unit_price = round(list_price * (1 - discount), 2)
                revenue = round(unit_price * qty, 2)
                cost = round(prod_cost[pid] * qty, 2)
                items.append((item_id, order_id, pid, qty, unit_price,
                              discount, revenue, cost))
                item_id += 1
            order_id += 1
        day += timedelta(days=1)

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.executescript(
        """
        CREATE TABLE regions (
            region_id   INTEGER PRIMARY KEY,
            region_name TEXT NOT NULL,
            country     TEXT NOT NULL
        );
        CREATE TABLE customers (
            customer_id   INTEGER PRIMARY KEY,
            customer_name TEXT NOT NULL,
            segment       TEXT NOT NULL,   -- Enterprise / SMB / Consumer
            region_id     INTEGER NOT NULL REFERENCES regions(region_id),
            signup_date   TEXT NOT NULL
        );
        CREATE TABLE products (
            product_id   INTEGER PRIMARY KEY,
            product_name TEXT NOT NULL,
            category     TEXT NOT NULL,
            unit_cost    REAL NOT NULL,
            unit_price   REAL NOT NULL
        );
        CREATE TABLE orders (
            order_id    INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
            order_date  TEXT NOT NULL,     -- YYYY-MM-DD
            channel     TEXT NOT NULL,     -- Online / Retail Store / Partner / Marketplace
            status      TEXT NOT NULL      -- Completed / Cancelled
        );
        CREATE TABLE order_items (
            order_item_id INTEGER PRIMARY KEY,
            order_id      INTEGER NOT NULL REFERENCES orders(order_id),
            product_id    INTEGER NOT NULL REFERENCES products(product_id),
            quantity      INTEGER NOT NULL,
            unit_price    REAL NOT NULL,   -- price actually paid, after discount
            discount      REAL NOT NULL,   -- 0.00 - 0.20
            revenue       REAL NOT NULL,   -- quantity * unit_price
            cost          REAL NOT NULL    -- quantity * unit_cost
        );
        CREATE INDEX idx_orders_date ON orders(order_date);
        CREATE INDEX idx_items_order ON order_items(order_id);
        CREATE INDEX idx_items_product ON order_items(product_id);
        """
    )
    cur.executemany("INSERT INTO regions VALUES (?,?,?)", REGIONS)
    cur.executemany("INSERT INTO customers VALUES (?,?,?,?,?)", customers)
    cur.executemany("INSERT INTO products VALUES (?,?,?,?,?)", products)
    cur.executemany("INSERT INTO orders VALUES (?,?,?,?,?)", orders)
    cur.executemany("INSERT INTO order_items VALUES (?,?,?,?,?,?,?,?)", items)

    # Convenience view most generated SQL will use.
    cur.executescript(
        """
        CREATE VIEW v_sales AS
        SELECT o.order_id,
               o.order_date,
               substr(o.order_date, 1, 7) AS order_month,
               o.channel,
               o.status,
               c.customer_id,
               c.customer_name,
               c.segment,
               r.region_name,
               p.product_id,
               p.product_name,
               p.category,
               i.quantity,
               i.unit_price,
               i.discount,
               i.revenue,
               i.cost,
               (i.revenue - i.cost) AS profit
        FROM orders o
        JOIN customers   c ON c.customer_id = o.customer_id
        JOIN regions     r ON r.region_id   = c.region_id
        JOIN order_items i ON i.order_id    = o.order_id
        JOIN products    p ON p.product_id  = i.product_id
        WHERE o.status = 'Completed';
        """
    )
    con.commit()

    rev = dict(cur.execute(
        "SELECT order_month, ROUND(SUM(revenue),2) FROM v_sales "
        "WHERE order_month IN ('2026-02','2026-03') GROUP BY 1").fetchall())
    elec = dict(cur.execute(
        "SELECT order_month, ROUND(SUM(revenue),2) FROM v_sales "
        "WHERE category='Electronics' AND order_month IN ('2026-02','2026-03') "
        "GROUP BY 1").fetchall())
    con.close()

    def pct(a, b):
        return (b - a) / a * 100 if a else 0.0

    print(f"DB written: {DB_PATH}")
    print(f"  orders={len(orders):,}  line_items={len(items):,}  "
          f"customers={len(customers)}  products={len(products)}")
    print(f"  Feb-2026 revenue = {rev.get('2026-02', 0):,.0f}")
    print(f"  Mar-2026 revenue = {rev.get('2026-03', 0):,.0f}  "
          f"({pct(rev.get('2026-02', 1), rev.get('2026-03', 0)):+.1f}%)")
    print(f"  Electronics MoM  = {pct(elec.get('2026-02', 1), elec.get('2026-03', 0)):+.1f}%")
    print(f"  at-risk accounts = "
          f"{[c[1] for c in customers if c[0] in at_risk_ids]}")


if __name__ == "__main__":
    main()
