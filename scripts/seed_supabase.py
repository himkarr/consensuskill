"""Load ``data/questions.json`` into a Supabase ``questions`` table.

Create the table first (Supabase SQL editor): ``supabase/questions.sql``.

Usage:
    SUPABASE_URL=https://<project>.supabase.co \\
    SUPABASE_SERVICE_KEY=<service role key> \\
    python scripts/seed_supabase.py

The service role key bypasses RLS - keep it out of git. ``SUPABASE_KEY`` is
accepted as a fallback (must be a key allowed to insert).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
BANK = ROOT / "data" / "questions.json"


def main() -> None:
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = (os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY", "")).strip()
    if not url or not key:
        sys.exit("set SUPABASE_URL and SUPABASE_SERVICE_KEY (see supabase/questions.sql)")

    raw = json.loads(BANK.read_text(encoding="utf-8"))
    rows = raw["questions"] if isinstance(raw, dict) else raw

    response = httpx.post(
        f"{url.rstrip('/')}/rest/v1/questions",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates",
        },
        params={"on_conflict": "id"},
        json=rows,
        timeout=15.0,
    )
    response.raise_for_status()
    print(f"seeded {len(rows)} questions into {url}")


if __name__ == "__main__":
    main()
