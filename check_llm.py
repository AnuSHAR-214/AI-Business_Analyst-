#!/usr/bin/env python3
"""
Verify the LLM connection before you rely on it.

    python check_llm.py

Tells you which provider was selected and why, then makes one real round-trip
so a bad key fails here rather than mid-demo.
"""

from __future__ import annotations

import sys

from app.config import settings
from app.llm import LLM


def mask(key: str) -> str:
    if not key:
        return "(not set)"
    return f"{key[:7]}…{key[-4:]}  ({len(key)} chars)"


def main() -> int:
    print("Configuration")
    print("-------------")
    print(f"  LLM_PROVIDER     {settings.llm_provider}")
    print(f"  OPENAI_API_KEY   {mask(settings.openai_api_key)}")
    print(f"  OPENAI_MODEL     {settings.openai_model}")
    print(f"  ANTHROPIC_API_KEY {mask(settings.anthropic_api_key)}")

    llm = LLM()
    print(f"\nSelected backend:  {llm.provider}  ({llm.model})")
    print("Selection log:")
    for reason in llm.reasons:
        print(f"  - {reason}")

    if llm.is_offline:
        print("""
Running OFFLINE (rule-based). The app still works, but SQL and narratives come
from templates rather than a model. Read the selection log above - it says
exactly which step failed. Common causes:

  1. "no API key set"       -> OPENAI_API_KEY is still blank in .env, or you
                               edited .env.example instead of .env
  2. "package not installed" -> pip install openai
  3. Nothing logged at all   -> you're running from the wrong directory; .env is
                               read from the folder containing app/
""")
        return 1

    print("\nSending a test request…")
    try:
        reply = llm.complete(
            "You are a SQL generator. Reply with SQL only, no prose.",
            "Return exactly: SELECT 1 AS ok",
            max_tokens=30,
        )
    except Exception as exc:
        print(f"  FAILED: {type(exc).__name__}: {exc}")
        print("""
If this is an authentication error the key is wrong or revoked.
If it mentions quota or billing, the key is valid but the account has no credit -
add credit, or set LLM_PROVIDER=offline to keep using the rule-based engine.
""")
        return 1

    print(f"  Response: {reply.text!r}")
    print(f"\nWorking. {llm.provider} ({llm.model}) will now write SQL and insights.")
    print("Next:  streamlit run streamlit_app.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
