#!/usr/bin/env python3
"""
Command-line interface.

    python ask.py "Why did revenue drop in March?"
    python ask.py                       # interactive REPL
"""

from __future__ import annotations

import sys

from app.pipeline import EXAMPLE_QUESTIONS, Analyst

RULE = "-" * 78


def show(ans) -> None:
    print(f"\n{RULE}\nQ: {ans.question}\n{RULE}")
    print(f"[plan]   {ans.plan.describe()}")
    print(f"[sql:{ans.sql_source}]\n{ans.sql}\n")
    if not ans.data.empty:
        with_pd_opts()
        print(ans.data.head(15).to_string(index=False))
        if len(ans.data) > 15:
            print(f"... {len(ans.data) - 15} more rows")
    print(f"\n[chart] {ans.chart.kind}")
    print(f"\n{ans.insight}\n")
    print(f"({ans.elapsed_s:.2f}s)")


def with_pd_opts() -> None:
    import pandas as pd
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 30)
    pd.set_option("display.float_format", lambda v: f"{v:,.2f}")


def main() -> None:
    analyst = Analyst()
    print(f"AI Business Analyst  |  LLM: {analyst.llm.provider} "
          f"({analyst.llm.model})  |  data {analyst.data_start} -> {analyst.data_end}")

    if len(sys.argv) > 1:
        show(analyst.ask(" ".join(sys.argv[1:])))
        return

    print("\nTry one of these, or type your own ('quit' to exit):")
    for q in EXAMPLE_QUESTIONS:
        print(f"  - {q}")
    while True:
        try:
            q = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if q.lower() in {"quit", "exit", "q"}:
            return
        if q:
            show(analyst.ask(q))


if __name__ == "__main__":
    main()
