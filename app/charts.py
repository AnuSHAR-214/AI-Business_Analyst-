"""
Automatic visualisation.

`choose()` decides *what* chart the data deserves; `to_plotly()` renders it.
The two are separate so the decision logic is unit-testable without a plotting
library installed, and so the same spec could be rendered by any front end.

Chart selection rules (in priority order):
  time column present            -> line
  diagnostic comparison          -> waterfall (contribution to change)
  one category + one number, <=25 -> horizontal bar, sorted
  two numeric columns             -> scatter
  single scalar                   -> big-number "metric" card
  otherwise                       -> table
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

TIME_HINTS = ("month", "date", "week", "quarter", "year", "period", "day")


@dataclass
class ChartSpec:
    kind: str                       # line | bar | waterfall | scatter | metric | table
    title: str = ""
    x: str | None = None
    y: str | None = None
    color: str | None = None
    data: pd.DataFrame = field(default_factory=pd.DataFrame)
    meta: dict[str, Any] = field(default_factory=dict)


def _numeric_cols(df: pd.DataFrame) -> list[str]:
    return df.select_dtypes(include="number").columns.tolist()


def _time_col(df: pd.DataFrame) -> str | None:
    for c in df.columns:
        if any(h in c.lower() for h in TIME_HINTS):
            return c
    return None


def choose(df: pd.DataFrame, title: str = "") -> ChartSpec:
    if df is None or df.empty:
        return ChartSpec(kind="table", title=title, data=pd.DataFrame())

    nums = _numeric_cols(df)
    cats = [c for c in df.columns if c not in nums]
    tcol = _time_col(df)

    if len(df) == 1 and len(nums) == 1 and not cats:
        return ChartSpec(kind="metric", title=title, y=nums[0], data=df)

    if tcol and nums:
        return ChartSpec(kind="line", title=title, x=tcol, y=nums[0],
                         color=cats[0] if cats and cats[0] != tcol else None,
                         data=df.sort_values(tcol))

    if len(cats) == 1 and nums and len(df) <= 25:
        d = df.sort_values(nums[0], ascending=True)
        return ChartSpec(kind="bar", title=title, x=nums[0], y=cats[0], data=d)

    if len(nums) >= 2 and len(df) > 3:
        return ChartSpec(kind="scatter", title=title, x=nums[0], y=nums[1],
                         color=cats[0] if cats else None, data=df)

    return ChartSpec(kind="table", title=title, data=df)


def waterfall_from_root_cause(rc, max_bars: int = 8) -> ChartSpec:
    """Contribution-to-change waterfall from a RootCause result."""
    rows = [{"label": rc.compare_label, "value": rc.prev_total, "measure": "absolute"}]
    seen, used = set(), 0.0
    for c in rc.contributors:
        if c.dimension != rc.contributors[0].dimension:
            continue                      # keep one dimension for a clean bridge
        if c.value in seen:
            continue
        seen.add(c.value)
        rows.append({"label": f"{c.value}", "value": c.delta, "measure": "relative"})
        used += c.delta
        if len(rows) >= max_bars:
            break
    residual = rc.delta - used
    if abs(residual) > 1e-6:
        rows.append({"label": "Everything else", "value": residual,
                     "measure": "relative"})
    rows.append({"label": rc.period_label, "value": rc.curr_total,
                 "measure": "total"})
    return ChartSpec(
        kind="waterfall",
        title=f"What moved {rc.metric_label}: {rc.compare_label} -> {rc.period_label}",
        data=pd.DataFrame(rows),
        meta={"dimension": rc.contributors[0].dimension if rc.contributors else ""},
    )


def to_plotly(spec: ChartSpec):
    """Render a spec with plotly. Imported lazily - plotly is optional."""
    import plotly.express as px
    import plotly.graph_objects as go

    df = spec.data
    if spec.kind == "line":
        fig = px.line(df, x=spec.x, y=spec.y, color=spec.color, markers=True,
                      title=spec.title)
    elif spec.kind == "bar":
        fig = px.bar(df, x=spec.x, y=spec.y, orientation="h", title=spec.title,
                     text_auto=".2s")
    elif spec.kind == "scatter":
        fig = px.scatter(df, x=spec.x, y=spec.y, color=spec.color,
                         title=spec.title)
    elif spec.kind == "waterfall":
        fig = go.Figure(go.Waterfall(
            orientation="v",
            measure=df["measure"].tolist(),
            x=df["label"].tolist(),
            y=df["value"].tolist(),
            connector={"line": {"color": "rgb(160,160,160)"}},
            decreasing={"marker": {"color": "#d1495b"}},
            increasing={"marker": {"color": "#2a9d8f"}},
            totals={"marker": {"color": "#4a5568"}},
        ))
        fig.update_layout(title=spec.title)
    else:
        return None

    fig.update_layout(margin=dict(l=10, r=10, t=50, b=10), height=420,
                      hovermode="x unified" if spec.kind == "line" else "closest")
    return fig
