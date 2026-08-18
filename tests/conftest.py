"""
Test isolation.

The suite must produce the same result on a machine with a funded API key, a
machine with a broken key, and a machine with no key at all. Without this file
it does not: `app.config.settings` reads `.env` at import time, so a developer's
real credentials leak into the tests, which then either behave differently or -
worse - spend real money.

So before any test runs we neutralise every credential source. Tests that need
a key present inject one explicitly via monkeypatch.

This is why `test_bad_key_falls_back_offline_with_a_reason` used to pass on a
clean checkout and fail as soon as someone filled in `.env`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402

# Strip credentials from both sources, once, before collection.
for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
    os.environ.pop(var, None)
settings.openai_api_key = ""
settings.anthropic_api_key = ""
settings.llm_provider = "offline"


@pytest.fixture
def no_credentials(monkeypatch):
    """Explicitly assert the clean state for tests that care about it."""
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return settings
