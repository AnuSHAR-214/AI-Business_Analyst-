#!/usr/bin/env python3
"""
One-command setup.

    python setup.py

Installs dependencies, builds the database, runs the tests and tells you what
to do next. Safe to re-run.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def step(n: int, total: int, msg: str) -> None:
    print(f"\n[{n}/{total}] {msg}")
    print("-" * (len(msg) + 8))


def run(cmd: list[str], optional: bool = False) -> bool:
    print("  $", " ".join(cmd))
    result = subprocess.run(cmd)
    if result.returncode != 0:
        if optional:
            print("  (skipped - optional step failed)")
            return False
        sys.exit(f"\nFailed: {' '.join(cmd)}")
    return True


def main() -> None:
    total = 4
    step(1, total, "Installing Python dependencies")
    run([sys.executable, "-m", "pip", "install", "-r",
         str(ROOT / "requirements.txt"), "-q"])

    step(2, total, "Generating the retail database")
    run([sys.executable, str(ROOT / "data" / "generate_data.py")])

    step(3, total, "Running the test suite")
    run([sys.executable, "-m", "pytest", str(ROOT / "tests"), "-q"], optional=True)

    step(4, total, "Ready")
    env = ROOT / ".env"
    if not env.exists():
        (ROOT / ".env").write_text((ROOT / ".env.example").read_text())
        print("  Created .env from .env.example (offline mode until you add a key).")
    print("""
  Start the web app:      streamlit run streamlit_app.py
  Or ask from the CLI:    python ask.py "Why did revenue drop in March?"

  Optional: put ANTHROPIC_API_KEY or OPENAI_API_KEY in .env to switch from the
  built-in rule engine to real LLM-generated SQL and narratives.
""")


if __name__ == "__main__":
    main()
