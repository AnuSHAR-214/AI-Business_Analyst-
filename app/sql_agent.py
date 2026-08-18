"""
Text-to-SQL agent.

Online path:  schema + plan hint -> LLM -> SQL -> guard -> execute
              on execution error, the error text is fed back once for a repair
Offline path: plan -> SQL template

Both paths return the same object, so nothing downstream cares which ran.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .database import Database
from .guard import UnsafeSQLError, strip_sql
from .llm import LLM
from .question import DIMENSIONS, Plan

SYSTEM_PROMPT = """You are a senior analytics engineer. You translate business \
questions into a single SQLite SELECT statement.

Hard rules:
- Return ONLY the SQL. No prose, no markdown fences, no explanation.
- Exactly one statement. SELECT or WITH only. Never INSERT/UPDATE/DELETE/DDL.
- Prefer the denormalised view v_sales; it is already filtered to completed orders.
- Always alias aggregates with clear snake_case names (e.g. AS revenue).
- Use substr(order_date,1,7) AS order_month for monthly grouping.
- Only use column values that appear in the schema's example values.
- Round money to 2 decimals. Order results meaningfully and LIMIT long outputs.
"""

REPAIR_PROMPT = """The SQL you produced failed. Fix it and return only the \
corrected SQL statement.

SQL:
{sql}

Error:
{error}
"""


@dataclass
class GeneratedSQL:
    sql: str
    source: str            # "llm" | "llm-repaired" | "template"
    plan: Plan
    notes: str = ""


class SQLAgent:
    def __init__(self, db: Database, llm: LLM) -> None:
        self.db = db
        self.llm = llm
        self._schema = db.schema_description()

    # ------------------------------------------------------------------ #
    def generate(self, question: str, plan: Plan) -> GeneratedSQL:
        if self.llm.is_offline:
            return GeneratedSQL(sql=build_template_sql(plan), source="template",
                                plan=plan,
                                notes="Offline template engine (no API key set).")

        user = (
            f"Database schema:\n{self._schema}\n\n"
            f"Parsed intent hint: {plan.describe()}\n\n"
            f"Business question: {question}\n\nSQL:"
        )
        try:
            sql = strip_sql(self.llm.complete(SYSTEM_PROMPT, user, 900).text)
            return GeneratedSQL(sql=sql, source="llm", plan=plan)
        except Exception as exc:                      # network / quota / SDK issue
            return GeneratedSQL(
                sql=build_template_sql(plan), source="template", plan=plan,
                notes=f"LLM unavailable ({type(exc).__name__}); used template engine.",
            )

    def run(self, question: str, plan: Plan) -> tuple[GeneratedSQL, pd.DataFrame]:
        gen = self.generate(question, plan)
        try:
            return gen, self.db.run(gen.sql).dataframe
        except Exception as exc:
            if gen.source != "llm":
                # template failed - that's a bug, surface it
                raise
            # one repair attempt, then fall back to the template
            try:
                fixed = strip_sql(
                    self.llm.complete(
                        SYSTEM_PROMPT,
                        REPAIR_PROMPT.format(sql=gen.sql, error=str(exc)),
                        900,
                    ).text
                )
                df = self.db.run(fixed).dataframe
                return GeneratedSQL(fixed, "llm-repaired", plan,
                                    notes=f"Repaired after: {exc}"), df
            except Exception as exc2:
                gen = GeneratedSQL(
                    build_template_sql(plan), "template", plan,
                    notes=f"LLM SQL failed ({exc2}); fell back to template.")
                return gen, self.db.run(gen.sql).dataframe


# --------------------------------------------------------------------------- #
def build_template_sql(plan: Plan) -> str:
    """Deterministic SQL for a parsed plan - the offline brain."""
    metric, alias = plan.metric_sql, plan.metric_alias
    where = plan.period.where if plan.period else "1=1"
    for col, val in plan.filters.items():
        where += f" AND {col} = '{val}'"

    if plan.intent == "trend":
        return (
            "SELECT order_month,\n"
            f"       ROUND({metric}, 2) AS {alias}\n"
            "FROM v_sales\n"
            f"WHERE {where}\n"
            "GROUP BY order_month\n"
            "ORDER BY order_month"
        )

    if plan.intent == "diagnostic":
        cur, prev = plan.period, plan.compare_to
        return (
            "SELECT CASE WHEN order_date BETWEEN "
            f"'{cur.start}' AND '{cur.end}' THEN '{cur.label}' "
            f"ELSE '{prev.label}' END AS period,\n"
            "       category,\n"
            "       region_name,\n"
            "       channel,\n"
            "       segment,\n"
            "       customer_name,\n"
            "       product_name,\n"
            f"       ROUND({metric}, 2) AS {alias}\n"
            "FROM v_sales\n"
            f"WHERE order_date BETWEEN '{prev.start}' AND '{prev.end}'\n"
            f"   OR order_date BETWEEN '{cur.start}' AND '{cur.end}'\n"
            "GROUP BY period, category, region_name, channel, segment, "
            "customer_name, product_name"
        )

    if plan.dimension:
        col, _ = DIMENSIONS[plan.dimension]
        order = "ASC" if plan.dimension == "month" else "DESC"
        limit = "" if plan.dimension == "month" else f"\nLIMIT {plan.top_n}"
        return (
            f"SELECT {col},\n"
            f"       ROUND({metric}, 2) AS {alias}\n"
            "FROM v_sales\n"
            f"WHERE {where}\n"
            f"GROUP BY {col}\n"
            f"ORDER BY {alias} {order}{limit}"
        )

    return (
        f"SELECT ROUND({metric}, 2) AS {alias}\n"
        "FROM v_sales\n"
        f"WHERE {where}"
    )


def build_breakdown_sql(plan: Plan, dimension: str, period) -> str:
    """Metric by one dimension for one period - the root-cause engine's unit query."""
    col, _ = DIMENSIONS[dimension]
    return (
        f"SELECT {col} AS dimension_value,\n"
        f"       {plan.metric_sql} AS value\n"
        "FROM v_sales\n"
        f"WHERE {period.where}\n"
        f"GROUP BY {col}"
    )
