"""
Question understanding.

Turns a free-text business question into a structured `Plan`:
intent + metric + dimension + time window. The plan is used two ways:

  1. offline, it drives a SQL template
  2. online, it is handed to the LLM as a hint alongside the schema, which
     measurably improves text-to-SQL accuracy versus prompting cold

Keeping this as its own layer means the app degrades gracefully rather than
collapsing when there is no API key.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})

METRICS = {
    "revenue": ("SUM(revenue)", "revenue", "Revenue"),
    "profit": ("SUM(profit)", "profit", "Profit"),
    "margin": ("ROUND(100.0 * SUM(profit) / NULLIF(SUM(revenue),0), 2)",
               "margin_pct", "Margin %"),
    "orders": ("COUNT(DISTINCT order_id)", "orders", "Orders"),
    "quantity": ("SUM(quantity)", "units", "Units sold"),
    "customers": ("COUNT(DISTINCT customer_id)", "customers", "Customers"),
    "aov": ("ROUND(SUM(revenue) / NULLIF(COUNT(DISTINCT order_id),0), 2)",
            "avg_order_value", "Average order value"),
    "discount": ("ROUND(AVG(discount) * 100, 2)", "avg_discount_pct",
                 "Average discount %"),
}

METRIC_WORDS = [
    ("aov", ("average order value", "aov", "basket size", "order value")),
    ("margin", ("margin", "profitability", "profit rate")),
    ("profit", ("profit", "gross profit", "contribution")),
    ("discount", ("discount", "markdown", "promo")),
    ("quantity", ("units", "quantity", "volume sold", "how many units")),
    ("customers", ("customers", "buyers", "accounts", "clients")),
    ("orders", ("orders", "transactions", "purchases", "order count")),
    ("revenue", ("revenue", "sales", "turnover", "top line", "gmv", "income")),
]

DIMENSIONS = {
    "category": ("category", "Category"),
    "region": ("region_name", "Region"),
    "channel": ("channel", "Channel"),
    "segment": ("segment", "Customer segment"),
    "product": ("product_name", "Product"),
    "customer": ("customer_name", "Customer"),
    "month": ("order_month", "Month"),
}

DIM_WORDS = [
    ("category", ("category", "categories", "product line", "product type")),
    ("region", ("region", "regions", "geography", "country", "market")),
    ("channel", ("channel", "channels", "online", "store", "marketplace",
                 "partner")),
    ("segment", ("segment", "segments", "enterprise", "smb", "consumer")),
    ("product", ("product", "products", "sku", "skus", "item")),
    ("customer", ("customer", "customers", "account", "accounts", "client")),
    ("month", ("month", "monthly", "over time", "trend", "by month",
               "month over month", "mom")),
]

DIAGNOSTIC_WORDS = ("why", "reason", "cause", "driver", "explain", "what happened",
                    "root cause", "behind the")
DIRECTION_DOWN = ("drop", "dropped", "decline", "declined", "fall", "fell",
                  "decrease", "decreased", "down", "slump", "dip", "lose", "lost",
                  "worse", "shrink", "shrank")
DIRECTION_UP = ("grow", "grew", "growth", "increase", "increased", "rise", "rose",
                "up", "spike", "jump", "jumped", "better", "improve", "improved")
RANK_WORDS = ("top", "best", "worst", "bottom", "highest", "lowest", "biggest",
              "largest", "smallest", "leading", "rank", "which")
TREND_WORDS = ("trend", "over time", "by month", "monthly", "month by month",
               "history", "trajectory", "seasonality")


@dataclass
class Period:
    """An inclusive [start, end] date window plus a human label."""
    start: str
    end: str
    label: str

    @property
    def where(self) -> str:
        return f"order_date BETWEEN '{self.start}' AND '{self.end}'"


@dataclass
class Plan:
    intent: str = "aggregate"          # aggregate | trend | rank | diagnostic
    metric: str = "revenue"
    dimension: str | None = None
    period: Period | None = None
    compare_to: Period | None = None
    direction: str = "down"            # for diagnostic questions
    top_n: int = 10
    filters: dict[str, str] = field(default_factory=dict)
    raw_question: str = ""

    @property
    def metric_sql(self) -> str:
        return METRICS[self.metric][0]

    @property
    def metric_alias(self) -> str:
        return METRICS[self.metric][1]

    @property
    def metric_label(self) -> str:
        return METRICS[self.metric][2]

    def describe(self) -> str:
        bits = [f"intent={self.intent}", f"metric={self.metric}"]
        if self.dimension:
            bits.append(f"dimension={self.dimension}")
        if self.period:
            bits.append(f"period={self.period.label}")
        if self.compare_to:
            bits.append(f"vs={self.compare_to.label}")
        if self.filters:
            bits.append("filters=" + ",".join(f"{k}={v}" for k, v in self.filters.items()))
        return "  ".join(bits)


# --------------------------------------------------------------------------- #
def _month_period(year: int, month: int) -> Period:
    last = calendar.monthrange(year, month)[1]
    return Period(
        start=f"{year:04d}-{month:02d}-01",
        end=f"{year:04d}-{month:02d}-{last:02d}",
        label=f"{calendar.month_name[month]} {year}",
    )


def _prev_month(p: Period) -> Period:
    y, m = int(p.start[:4]), int(p.start[5:7])
    y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return _month_period(y, m)


def _shift_months(iso: str, months: int) -> str:
    y, m = int(iso[:4]), int(iso[5:7])
    total = y * 12 + (m - 1) + months
    return f"{total // 12:04d}-{total % 12 + 1:02d}-01"


def _find_metric(q: str) -> str:
    for key, words in METRIC_WORDS:
        if any(w in q for w in words):
            return key
    return "revenue"


def _find_dimension(q: str) -> str | None:
    # "by X" / "per X" is the strongest signal
    m = re.search(r"\b(?:by|per|across|split by|broken down by)\s+([a-z ]{3,20})", q)
    if m:
        tail = m.group(1)
        for key, words in DIM_WORDS:
            if any(w in tail for w in words):
                return key
    for key, words in DIM_WORDS:
        if any(re.search(rf"\b{re.escape(w)}\b", q) for w in words):
            return key
    return None


def _find_periods(q: str, data_end: str) -> tuple[Period | None, Period | None]:
    max_year = int(data_end[:4])

    # "in March 2026" / "March 2026" / "Mar-2026"
    m = re.search(r"\b([a-z]{3,9})[\s\-/]+(\d{4})\b", q)
    if m and m.group(1) in MONTHS:
        return _month_period(int(m.group(2)), MONTHS[m.group(1)]), None

    # "Q1 2026" or "Q1"
    m = re.search(r"\bq([1-4])\b(?:\s*(?:of\s*)?(\d{4}))?", q)
    if m:
        quarter = int(m.group(1))
        year = int(m.group(2)) if m.group(2) else max_year
        sm, em = quarter * 3 - 2, quarter * 3
        return (
            Period(f"{year}-{sm:02d}-01",
                   f"{year}-{em:02d}-{calendar.monthrange(year, em)[1]:02d}",
                   f"Q{quarter} {year}"),
            None,
        )

    # bare month name -> latest year in the data that has that month
    for name, num in MONTHS.items():
        if len(name) > 3 and re.search(rf"\b{name}\b", q):
            year = max_year if f"{max_year}-{num:02d}" <= data_end[:7] else max_year - 1
            return _month_period(year, num), None

    # "last N months"
    m = re.search(r"\blast\s+(\d{1,2})\s+months?\b", q)
    if m:
        n = int(m.group(1))
        start = _shift_months(data_end, -(n - 1))
        return Period(start, data_end, f"last {n} months"), None

    # bare year
    m = re.search(r"\b(20\d{2})\b", q)
    if m:
        y = int(m.group(1))
        return Period(f"{y}-01-01", f"{y}-12-31", str(y)), None

    return None, None


def parse(question: str, data_start: str, data_end: str) -> Plan:
    q = question.lower().strip()
    plan = Plan(raw_question=question)
    plan.metric = _find_metric(q)
    plan.dimension = _find_dimension(q)

    period, compare = _find_periods(q, data_end)
    plan.period = period
    plan.compare_to = compare

    if any(w in q for w in DIAGNOSTIC_WORDS) and (
        any(w in q for w in DIRECTION_DOWN) or any(w in q for w in DIRECTION_UP)
        or "why" in q
    ):
        plan.intent = "diagnostic"
        plan.direction = "up" if (
            any(w in q for w in DIRECTION_UP) and not any(w in q for w in DIRECTION_DOWN)
        ) else "down"
        if plan.period is None:                      # default to the latest month
            y, m = int(data_end[:4]), int(data_end[5:7])
            plan.period = _month_period(y, m)
        plan.compare_to = plan.compare_to or _prev_month(plan.period)
        plan.dimension = None                        # diagnostics scan every dimension
    elif any(w in q for w in TREND_WORDS) or plan.dimension == "month":
        plan.intent = "trend"
        plan.dimension = "month"
    elif any(re.search(rf"\b{w}\b", q) for w in RANK_WORDS):
        plan.intent = "rank"
        plan.dimension = plan.dimension or "product"

    m = re.search(r"\btop\s+(\d{1,3})\b", q)
    if m:
        plan.top_n = int(m.group(1))

    if plan.period is None:
        plan.period = Period(data_start, data_end, "all time")

    return plan
