#!/usr/bin/env python3
"""
Build the single-file web demo.

    python web/build.py     ->  docs/index.html

Inlines docs/web_data.json into web/template.html so the result is one
self-contained file: no server, no build tool, no CDN, works offline and from
file://. That also makes it deployable to GitHub Pages by committing docs/.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "web" / "template.html"
DATA = ROOT / "docs" / "web_data.json"
OUT = ROOT / "docs" / "index.html"
TOKEN = "/*__DATA__*/{}"


def main() -> None:
    if not DATA.exists():
        print("web_data.json missing - generating it first…")
        subprocess.run([sys.executable, str(ROOT / "data" / "export_web.py")],
                       check=True)

    html = TEMPLATE.read_text(encoding="utf-8")
    if TOKEN not in html:
        sys.exit(f"Token {TOKEN!r} not found in {TEMPLATE}")

    payload = json.dumps(json.loads(DATA.read_text(encoding="utf-8")),
                         separators=(",", ":"))
    # </script> inside JSON data would close the tag early
    payload = payload.replace("</", "<\\/")

    OUT.write_text(html.replace(TOKEN, payload), encoding="utf-8")
    print(f"Built {OUT}  ({OUT.stat().st_size / 1024:.0f} KB, single file)")
    print(f"Open it directly:  file://{OUT}")


if __name__ == "__main__":
    main()
