"""
Analysis engine.

Two jobs:

1. `profile()` - describe any result table (totals, extremes, concentration,
   period-over-period change) so the narrative layer has facts to talk about.

2. `root_cause()` - the interesting one. Given "metric moved between period A
   and period B", it decomposes the change across every available dimension and
   ranks the segments by *contribution to the change*, not by size. That is the
   difference between "Electronics is our biggest category" (useless) and
   "Electronics explains 68% of the drop" (an answer).

All numbers here come from SQL aggregates over the real database. Nothing is
estimated, and the LLM never invents a figure - it only phrases these facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .database import Database
from .question import DIMENSIONS, Plan
from .sql_agent import build_breakdown_sql

DIAGNOSTIC_DIMENSIONS = ["category", "region", "channel", "segment",
                         "customer", "product"]


# --------------------------------------------------------------------------- #
@dataclass
class Contributor:
    dimension: str
    label: str
    value: str
    prev: float
    curr: float
    delta: float
    pct_change: float
    share_of_change: float      # fraction of the total movement explained

    def sentence(self, unit: str = "") -> str:
        arrow = "fell" if self.delta < 0 else "rose"
        return (
            f"{self.label} '{self.value}' {arrow} {abs(self.pct_change):.1f}% "
            f"({unit}{self.prev:,.0f} -> {unit}{self.curr:,.0f}), "
            f"{abs(self.share_of_change) * 100:.0f}% of the total change"
        )


@dataclass
class RootCause:
    metric_label: str
    period_label: str
    compare_label: str
    prev_total: float
    curr_total: float
    delta: float
    pct_change: float
    contributors: list[Contributor] = field(default_factory=list)
    by_dimension: dict[str, pd.DataFrame] = field(default_factory=dict)
    monthly: pd.DataFrame | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def direction(self) -> str:
        return "decreased" if self.delta < 0 else "increased"

    def headline(self) -> str:
        return (
            f"{self.metric_label} {self.direction} {abs(self.pct_change):.1f}% "
            f"in {self.period_label} vs {self.compare_label} "
            f"({self.prev_total:,.0f} -> {self.curr_total:,.0f}, "
            f"{self.delta:+,.0f})"
        )

    def to_facts(self, limit: int = 8) -> str:
        """Compact, LLM-friendly evidence block."""
        lines = [self.headline(), "", "Largest contributors to the change:"]
        for c in self.contributors[:limit]:
            lines.append(f"  - [{c.dimension}] {c.sentence()}")
        if self.monthly is not None and len(self.monthly) > 1:
            recent = self.monthly.tail(6)
            lines.append("")
            lines.append("Recent monthly trend: " + ", ".join(
                f"{r.order_month}={r.value:,.0f}" for r in recent.itertuples()
            ))
        lines.extend(f"  ! {n}" for n in self.notes)
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
class Analyzer:
    def __init__(self, db: Database) -> None:
        self.db = db

    # -- generic result profiling -----------------------------------------
    @staticmethod
    def profile(df: pd.DataFrame, plan: Plan) -> str:
        if df.empty:
            return "The query returned no rows."

        num_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        cat_cols = [c for c in df.columns if c not in num_cols]
        out: list[str] = [f"{len(df)} row(s), columns: {', '.join(df.columns)}."]

        for col in num_cols[:3]:
            s = df[col].dropna()
            if s.empty:
                continue
            out.append(
                f"{col}: total={s.sum():,.2f}  mean={s.mean():,.2f}  "
                f"min={s.min():,.2f}  max={s.max():,.2f}"
            )
            if cat_cols and len(df) > 1:
                key = cat_cols[0]
                top = df.nlargest(min(5, len(df)), col)
                out.append(
                    f"Top {key} by {col}: " + ", ".join(
                        f"{r[key]} ({r[col]:,.0f})" for _, r in top.iterrows()
                    )
                )
                total = s.sum()
                if total:
                    share = top[col].sum() / total * 100
                    out.append(f"Those account for {share:.1f}% of {col}.")

        # month-over-month if this looks like a time series
        month_col = next((c for c in df.columns
                          if c.lower() in ("order_month", "month", "period")), None)
        if month_col and num_cols and len(df) > 1:
            s = df.sort_values(month_col)
            first, last = s[num_cols[0]].iloc[0], s[num_cols[0]].iloc[-1]
            if first:
                out.append(
                    f"{num_cols[0]} moved from {first:,.0f} ({s[month_col].iloc[0]}) "
                    f"to {last:,.0f} ({s[month_col].iloc[-1]}), "
                    f"{(last - first) / first * 100:+.1f}%."
                )
        return "\n".join(out)

    # -- root cause --------------------------------------------------------
    def root_cause(self, plan: Plan,
                   dimensions: list[str] | None = None,
                   min_share: float = 0.03,
                   per_dimension_k: int = 3,
                   top_k: int = 15) -> RootCause:
        assert plan.period and plan.compare_to, "diagnostic plans need two periods"
        dims = dimensions or DIAGNOSTIC_DIMENSIONS

        prev_total = self._scalar(plan, plan.compare_to)
        curr_total = self._scalar(plan, plan.period)
        delta = curr_total - prev_total
        pct = (delta / prev_total * 100) if prev_total else 0.0

        rc = RootCause(
            metric_label=plan.metric_label,
            period_label=plan.period.label,
            compare_label=plan.compare_to.label,
            prev_total=prev_total, curr_total=curr_total,
            delta=delta, pct_change=pct,
        )

        contributors: list[Contributor] = []
        for dim in dims:
            if dim not in DIMENSIONS:
                continue
            merged = self._compare_dimension(plan, dim)
            rc.by_dimension[dim] = merged

            per_dim: list[Contributor] = []
            for _, row in merged.iterrows():
                share = (row["delta"] / delta) if delta else 0.0
                # keep only segments moving in the same direction as the
                # headline change, and by a non-trivial amount
                if share < min_share:
                    continue
                per_dim.append(Contributor(
                    dimension=dim,
                    label=DIMENSIONS[dim][1],
                    value=str(row["dimension_value"]),
                    prev=float(row["prev"]), curr=float(row["curr"]),
                    delta=float(row["delta"]),
                    pct_change=float(row["pct_change"]),
                    share_of_change=float(share),
                ))
            # keep the strongest few per dimension so a wide dimension
            # (products, customers) can't crowd out a narrow one (region)
            per_dim.sort(key=lambda c: -abs(c.share_of_change))
            contributors.extend(per_dim[:per_dimension_k])

        contributors.sort(key=lambda c: -abs(c.share_of_change))
        rc.contributors = contributors[:top_k]

        rc.monthly = self._monthly(plan)
        rc.notes = self._sanity_notes(plan, rc)
        return rc

    # -- helpers -----------------------------------------------------------
    def _scalar(self, plan: Plan, period) -> float:
        sql = (f"SELECT {plan.metric_sql} AS value FROM v_sales "
               f"WHERE {period.where}")
        df = self.db.run(sql).dataframe
        return float(df["value"].iloc[0] or 0) if len(df) else 0.0

    def _compare_dimension(self, plan: Plan, dim: str) -> pd.DataFrame:
        prev = self.db.run(build_breakdown_sql(plan, dim, plan.compare_to)).dataframe
        curr = self.db.run(build_breakdown_sql(plan, dim, plan.period)).dataframe
        merged = prev.merge(curr, on="dimension_value", how="outer",
                            suffixes=("_prev", "_curr")).fillna(0)
        merged = merged.rename(columns={"value_prev": "prev", "value_curr": "curr"})
        merged["delta"] = merged["curr"] - merged["prev"]
        merged["pct_change"] = np.where(
            merged["prev"] != 0, merged["delta"] / merged["prev"] * 100, np.nan)
        return merged.sort_values("delta")

    def _monthly(self, plan: Plan) -> pd.DataFrame:
        sql = (
            "SELECT substr(order_date,1,7) AS order_month, "
            f"{plan.metric_sql} AS value FROM v_sales "
            "GROUP BY 1 ORDER BY 1"
        )
        return self.db.run(sql).dataframe

    def _sanity_notes(self, plan: Plan, rc: RootCause) -> list[str]:
        """Checks a careful analyst would run before publishing a number."""
        notes: list[str] = []

        # 1. calendar-length distortion
        d_curr = (pd.Timestamp(plan.period.end) - pd.Timestamp(plan.period.start)).days + 1
        d_prev = (pd.Timestamp(plan.compare_to.end)
                  - pd.Timestamp(plan.compare_to.start)).days + 1
        if d_curr != d_prev:
            adj = rc.curr_total / d_curr * d_prev
            adj_pct = (adj - rc.prev_total) / rc.prev_total * 100 if rc.prev_total else 0
            notes.append(
                f"Periods differ in length ({d_prev} vs {d_curr} days); "
                f"on a per-day basis the change is {adj_pct:+.1f}%."
            )

        # 2. cancellations (v_sales excludes them, so they hide the true demand)
        sql = (
            "SELECT substr(order_date,1,7) AS m, "
            "ROUND(100.0*SUM(status='Cancelled')/COUNT(*),2) AS cancel_rate "
            "FROM orders WHERE order_date BETWEEN "
            f"'{plan.compare_to.start}' AND '{plan.period.end}' GROUP BY 1 ORDER BY 1"
        )
        cr = self.db.run(sql).dataframe
        if len(cr) >= 2:
            a, b = float(cr["cancel_rate"].iloc[0]), float(cr["cancel_rate"].iloc[-1])
            if b - a > 0.5:
                notes.append(
                    f"Order cancellation rate rose from {a:.1f}% to {b:.1f}% "
                    "over the same window."
                )

        # 3. is this just seasonality? compare with the same month last year
        if rc.monthly is not None and len(rc.monthly) > 12:
            m = rc.monthly.set_index("order_month")["value"]
            key = plan.period.start[:7]
            ly = f"{int(key[:4]) - 1}-{key[5:7]}"
            if key in m.index and ly in m.index and m[ly]:
                yoy = (m[key] - m[ly]) / m[ly] * 100
                notes.append(f"Year-over-year, the same month is {yoy:+.1f}%.")
        return notes
