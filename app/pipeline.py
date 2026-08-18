"""
Orchestrator - the one public entry point.

    answer = Analyst().ask("Why did revenue drop in March?")

Flow:
    question -> parse plan
             -> diagnostic?  yes -> root-cause decomposition -> waterfall
                             no  -> text-to-SQL -> execute -> profile -> chart
             -> narrative insight
             -> Answer(sql, data, chart, insight, plan, trace)

`trace` records every step, which is what makes the app debuggable and what you
show an interviewer when they ask "how do you know it did the right thing".
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import pandas as pd

from . import charts
from .analyzer import Analyzer, RootCause
from .charts import ChartSpec
from .database import Database
from .insights import InsightWriter
from .llm import LLM
from .question import Plan, parse
from .sql_agent import SQLAgent

EXAMPLE_QUESTIONS = [
    "Why did revenue drop in March?",
    "Show me monthly revenue for the last 12 months",
    "What are the top 10 products by revenue in 2026?",
    "Revenue by region for Q1 2026",
    "Which customers spent the most in 2026?",
    "What is the profit margin by category?",
    "How did average order value trend over time?",
    "Why did revenue grow in November 2025?",
]


@dataclass
class Answer:
    question: str
    plan: Plan
    sql: str
    sql_source: str
    data: pd.DataFrame
    chart: ChartSpec
    insight: str
    facts: str
    root_cause: RootCause | None = None
    extra_charts: list[ChartSpec] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    error: str | None = None


class Analyst:
    def __init__(self, db_path: str | None = None,
                 provider: str | None = None) -> None:
        self.db = Database(db_path)
        self.llm = LLM(provider)
        self.agent = SQLAgent(self.db, self.llm)
        self.analyzer = Analyzer(self.db)
        self.writer = InsightWriter(self.llm)
        self.data_start, self.data_end = self.db.date_range()

    # ------------------------------------------------------------------ #
    def ask(self, question: str) -> Answer:
        t0 = time.perf_counter()
        trace: list[str] = [f"provider={self.llm.provider} model={self.llm.model}"]

        plan = parse(question, self.data_start, self.data_end)
        trace.append(f"plan: {plan.describe()}")

        try:
            if plan.intent == "diagnostic":
                answer = self._diagnostic(question, plan, trace)
            else:
                answer = self._standard(question, plan, trace)
        except Exception as exc:                       # never crash the UI
            answer = Answer(
                question=question, plan=plan, sql="", sql_source="none",
                data=pd.DataFrame(), chart=ChartSpec(kind="table"),
                insight=f"Sorry - I couldn't answer that. ({exc})",
                facts="", trace=trace, error=str(exc),
            )

        answer.elapsed_s = time.perf_counter() - t0
        return answer

    # -- branches ----------------------------------------------------------
    def _diagnostic(self, question: str, plan: Plan, trace: list[str]) -> Answer:
        trace.append("branch: root-cause decomposition")
        rc = self.analyzer.root_cause(plan)
        trace.append(f"scanned dimensions: {', '.join(rc.by_dimension)}")
        trace.append(f"headline: {rc.headline()}")

        table = pd.DataFrame([{
            "dimension": c.dimension,
            "segment": c.value,
            plan.compare_to.label: round(c.prev, 2),
            plan.period.label: round(c.curr, 2),
            "change": round(c.delta, 2),
            "change_%": round(c.pct_change, 1),
            "share_of_total_change_%": round(c.share_of_change * 100, 1),
        } for c in rc.contributors])

        main = charts.waterfall_from_root_cause(rc) if rc.contributors else \
            ChartSpec(kind="table", data=table)

        extra: list[ChartSpec] = []
        if rc.monthly is not None and not rc.monthly.empty:
            extra.append(ChartSpec(
                kind="line", title=f"{plan.metric_label} by month",
                x="order_month", y="value", data=rc.monthly))
        for dim in ("category", "region"):
            d = rc.by_dimension.get(dim)
            if d is not None and not d.empty:
                melted = d[["dimension_value", "prev", "curr"]].rename(
                    columns={"prev": plan.compare_to.label,
                             "curr": plan.period.label})
                extra.append(ChartSpec(
                    kind="table",
                    title=f"{plan.metric_label} by {dim}", data=melted))

        facts = rc.to_facts()
        insight = self.writer.diagnostic(question, rc)
        trace.append(f"insight written by {self.llm.provider}")

        return Answer(question=question, plan=plan,
                      sql="-- root-cause decomposition: one grouped query per "
                          "dimension per period",
                      sql_source="analyzer", data=table, chart=main,
                      insight=insight, facts=facts, root_cause=rc,
                      extra_charts=extra, trace=trace)

    def _standard(self, question: str, plan: Plan, trace: list[str]) -> Answer:
        gen, df = self.agent.run(question, plan)
        trace.append(f"sql source: {gen.source}" + (f" ({gen.notes})" if gen.notes else ""))
        trace.append(f"rows returned: {len(df)}")

        facts = self.analyzer.profile(df, plan)
        spec = charts.choose(df, title=question.strip().rstrip("?"))
        trace.append(f"chart: {spec.kind}")
        insight = self.writer.summarise(question, gen.sql, facts)

        return Answer(question=question, plan=plan, sql=gen.sql,
                      sql_source=gen.source, data=df, chart=spec,
                      insight=insight, facts=facts, trace=trace)
