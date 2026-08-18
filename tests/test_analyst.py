"""
End-to-end test suite. Runs entirely offline - no API key, no network.

    python -m pytest tests -q

What is covered:
  * the SQL guard actually blocks writes and injection-ish payloads
  * the question parser produces the right plan for realistic phrasings
  * the root-cause engine finds the *planted* answer in the generated data
  * the full pipeline returns a populated Answer for every example question
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import charts                                    # noqa: E402
from app.database import Database                         # noqa: E402
from app.guard import UnsafeSQLError, validate_sql        # noqa: E402
from app.pipeline import EXAMPLE_QUESTIONS, Analyst       # noqa: E402
from app.question import parse                            # noqa: E402


@pytest.fixture(scope="session")
def analyst() -> Analyst:
    return Analyst(provider="offline")


@pytest.fixture(scope="session")
def db(analyst) -> Database:
    return analyst.db


# --- guard ----------------------------------------------------------------
@pytest.mark.parametrize("bad", [
    "DELETE FROM orders",
    "DROP TABLE customers",
    "UPDATE products SET unit_price = 0",
    "SELECT 1; DROP TABLE orders",
    "INSERT INTO regions VALUES (9,'x','y')",
    "PRAGMA table_info(orders)",
    "ATTACH DATABASE 'x.db' AS x",
    "",
])
def test_guard_blocks_writes(bad):
    with pytest.raises(UnsafeSQLError):
        validate_sql(bad)


@pytest.mark.parametrize("good", [
    "SELECT 1",
    "select category, sum(revenue) from v_sales group by 1",
    "WITH t AS (SELECT 1 AS a) SELECT a FROM t",
    "```sql\nSELECT 1\n```",
    "SELECT customer_name FROM v_sales WHERE segment = 'Enterprise' -- note",
])
def test_guard_allows_reads(good):
    assert validate_sql(good)


def test_guard_ignores_keywords_inside_string_literals():
    assert validate_sql("SELECT * FROM v_sales WHERE product_name = 'drop kit'")


def test_database_is_read_only(db):
    with pytest.raises(Exception):
        with db._connect() as con:
            con.execute("DELETE FROM orders")


# --- question parsing -----------------------------------------------------
def test_parse_diagnostic(analyst):
    p = parse("Why did revenue drop in March 2026?",
              analyst.data_start, analyst.data_end)
    assert p.intent == "diagnostic"
    assert p.metric == "revenue"
    assert p.period.start == "2026-03-01" and p.period.end == "2026-03-31"
    assert p.compare_to.start == "2026-02-01"


def test_parse_bare_month_resolves_to_latest_year(analyst):
    p = parse("why did sales fall in march", analyst.data_start, analyst.data_end)
    assert p.period.start.startswith("2026-03")


def test_parse_rank_and_trend(analyst):
    r = parse("top 5 products by revenue in 2026",
              analyst.data_start, analyst.data_end)
    assert r.intent == "rank" and r.dimension == "product" and r.top_n == 5

    t = parse("show monthly revenue trend", analyst.data_start, analyst.data_end)
    assert t.intent == "trend" and t.dimension == "month"


def test_parse_metrics(analyst):
    for text, expected in [
        ("what is our profit by region", "profit"),
        ("average order value by channel", "aov"),
        ("how many orders did we get in Q1 2026", "orders"),
        ("margin by category", "margin"),
    ]:
        assert parse(text, analyst.data_start, analyst.data_end).metric == expected


# --- the planted story ----------------------------------------------------
def test_root_cause_finds_the_march_drop(analyst):
    ans = analyst.ask("Why did revenue drop in March 2026?")
    rc = ans.root_cause
    assert rc is not None, "diagnostic branch should have run"

    # headline: a real, material decline
    assert rc.delta < 0
    assert -30 < rc.pct_change < -10, f"unexpected headline change {rc.pct_change}"

    # Electronics must be the single biggest contributor
    cats = [c for c in rc.contributors if c.dimension == "category"]
    assert cats, "no category contributors found"
    assert cats[0].value == "Electronics"
    assert cats[0].pct_change < -15

    # the two churned enterprise accounts must surface
    custs = [c for c in rc.contributors if c.dimension == "customer"]
    assert len(custs) >= 2
    assert all(c.delta < 0 for c in custs[:2])

    # contributions are shares of the real movement, not of the level
    assert all(0 < c.share_of_change <= 1.5 for c in rc.contributors)


def test_root_cause_numbers_reconcile_with_sql(analyst, db):
    ans = analyst.ask("Why did revenue drop in March 2026?")
    rc = ans.root_cause
    direct = db.run(
        "SELECT ROUND(SUM(revenue),2) v FROM v_sales "
        "WHERE order_date BETWEEN '2026-03-01' AND '2026-03-31'"
    ).dataframe["v"].iloc[0]
    assert abs(rc.curr_total - float(direct)) < 0.01


def test_category_contributions_sum_to_category_delta(analyst):
    ans = analyst.ask("Why did revenue drop in March 2026?")
    d = ans.root_cause.by_dimension["category"]
    assert abs(d["delta"].sum() - ans.root_cause.delta) < 1.0


def test_insight_mentions_the_real_drivers(analyst):
    ans = analyst.ask("Why did revenue drop in March 2026?")
    assert "Electronics" in ans.insight
    assert "%" in ans.insight
    assert len(ans.insight) > 120


# --- pipeline breadth -----------------------------------------------------
@pytest.mark.parametrize("question", EXAMPLE_QUESTIONS)
def test_every_example_question_answers(analyst, question):
    ans = analyst.ask(question)
    assert ans.error is None, ans.error
    assert ans.insight.strip()
    assert ans.chart.kind in {"line", "bar", "waterfall", "scatter",
                              "metric", "table"}
    if ans.plan.intent != "diagnostic":
        assert ans.sql.lower().startswith(("select", "with"))
        assert not ans.data.empty


def test_chart_selection_rules(analyst):
    import pandas as pd
    ts = pd.DataFrame({"order_month": ["2026-01", "2026-02"], "revenue": [1, 2]})
    assert charts.choose(ts).kind == "line"

    cat = pd.DataFrame({"category": ["A", "B"], "revenue": [3, 4]})
    assert charts.choose(cat).kind == "bar"

    scalar = pd.DataFrame({"revenue": [42]})
    assert charts.choose(scalar).kind == "metric"

    assert charts.choose(pd.DataFrame()).kind == "table"


def test_trend_query_is_ordered_and_complete(analyst):
    ans = analyst.ask("Show me monthly revenue for the last 12 months")
    months = ans.data.iloc[:, 0].tolist()
    assert months == sorted(months)
    assert len(months) >= 11


def test_unanswerable_question_degrades_gracefully(analyst):
    ans = analyst.ask("qwerty zxcv")
    assert ans.insight.strip()          # never an empty response


# --- API key validation ---------------------------------------------------
@pytest.mark.parametrize("bad,expect", [
    ("pip install openai && python check_llm.py", "command"),
    ('"sk-proj-abcdefghijklmnopqrstuv"', "quotes"),
    ("your-key-here", "placeholder"),
    ("ghp_abcdefghijklmnopqrstuvwx", "should start with"),
    ("sk-short", "truncated"),
])
def test_malformed_keys_are_explained_not_swallowed(bad, expect):
    from app.llm import key_problem
    problem = key_problem("openai", bad)
    assert problem and expect in problem


def test_valid_looking_keys_pass_validation():
    from app.llm import key_problem
    assert key_problem("openai", "sk-proj-abcdefghijklmnopqrstuvwxyz123456") is None
    assert key_problem("anthropic", "sk-ant-api03-abcdefghijklmnopqrstuvwxyz") is None


def test_bad_key_falls_back_offline_with_a_reason(monkeypatch, no_credentials):
    """
    A malformed key must degrade to offline AND say why.

    Note we patch `settings`, not os.environ: settings is populated from .env at
    import time and takes precedence, so setting the environment variable alone
    would be silently ignored on any machine that has a real key in .env.
    """
    from app.config import settings
    from app.llm import LLM

    monkeypatch.setattr(settings, "openai_api_key", "pip install openai")
    llm = LLM("openai")
    assert llm.is_offline
    assert any("command" in r for r in llm.reasons), llm.reasons


def test_no_credentials_means_offline_not_a_crash(no_credentials):
    from app.llm import LLM
    llm = LLM("auto")
    assert llm.is_offline
    assert any("no API key set" in r for r in llm.reasons)


def test_suite_never_sees_real_credentials():
    """Guards the guard: if this fails, tests could spend the developer's money."""
    import os

    from app.config import settings
    assert not settings.openai_api_key
    assert not settings.anthropic_api_key
    assert not os.environ.get("OPENAI_API_KEY")
    assert not os.environ.get("ANTHROPIC_API_KEY")


if __name__ == "__main__":
    raise SystemExit(pytest.main([os.path.dirname(__file__), "-q"]))
