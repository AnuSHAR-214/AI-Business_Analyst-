"""Central configuration. Everything is env-overridable, nothing is required."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Tiny .env loader so we don't need python-dotenv as a hard dependency."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


@dataclass
class Settings:
    db_path: str = field(
        default_factory=lambda: os.environ.get(
            "RETAIL_DB_PATH", str(ROOT / "data" / "retail.db")
        )
    )
    # "auto" picks anthropic -> openai -> offline, whichever is available.
    llm_provider: str = field(
        default_factory=lambda: os.environ.get("LLM_PROVIDER", "auto").lower()
    )
    anthropic_api_key: str = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", "")
    )
    openai_api_key: str = field(
        default_factory=lambda: os.environ.get("OPENAI_API_KEY", "")
    )
    anthropic_model: str = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
    )
    openai_model: str = field(
        default_factory=lambda: os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    )
    max_rows: int = int(os.environ.get("MAX_ROWS", "2000"))
    query_timeout_s: int = int(os.environ.get("QUERY_TIMEOUT_S", "20"))


settings = Settings()
