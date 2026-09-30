"""Load ``data/questions.json`` into a Supabase ``questions`` table.

Preferred path (direct Postgres - creates the table itself):

    SUPABASE_DB_URL=postgresql://... python scripts/seed_supabase.py

REST path (create the table first with ``supabase/questions.sql``):

    SUPABASE_URL=https://<project>.supabase.co \\
    SUPABASE_SERVICE_KEY=<service role key> \\
    python scripts/seed_supabase.py

The service role key bypasses RLS - keep it out of git. ``SUPABASE_KEY``
is accepted as a fallback (must be a key allowed to insert).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
BANK = ROOT / "data" / "questions.json"

DDL_STATEMENTS = [
    """
    create table if not exists public.questions (
      id        text primary key,
      text      text not null,
      option_a  text not null,
      option_b  text not null
    )
    """,
    "alter table public.questions enable row level security",
    'drop policy if exists "questions are publicly readable" on public.questions',
    """
    create policy "questions are publicly readable"
      on public.questions
      for select
      using (true)
    """,
]


def load_rows() -> list[dict]:
    raw = json.loads(BANK.read_text(encoding="utf-8"))
    return raw["questions"] if isinstance(raw, dict) else raw


def seed_postgres(dsn: str, rows: list[dict]) -> None:
    """Create the table (if needed) and upsert the bank via psycopg."""
    import psycopg  # lazy: only needed when SUPABASE_DB_URL is configured

    # prepare_threshold=None: prepared statements break on Supabase's
    # transaction pooler (pgbouncer), which is what IPv4-only hosts use.
    with psycopg.connect(dsn, autocommit=True, prepare_threshold=None) as conn:
        with conn.cursor() as cur:
            for statement in DDL_STATEMENTS:
                cur.execute(statement)
            for row in rows:
                cur.execute(
                    """
                    insert into public.questions (id, text, option_a, option_b)
                    values (%(id)s, %(text)s, %(option_a)s, %(option_b)s)
                    on conflict (id) do update
                      set text = excluded.text,
                          option_a = excluded.option_a,
                          option_b = excluded.option_b
                    """,
                    row,
                )
    print(f"seeded {len(rows)} questions into postgres")


def seed_rest(url: str, key: str, rows: list[dict]) -> None:
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


def main() -> None:
    rows = load_rows()
    db_url = os.environ.get("SUPABASE_DB_URL", "").strip()
    if db_url:
        seed_postgres(db_url, rows)
        return

    url = os.environ.get("SUPABASE_URL", "").strip()
    key = (os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY", "")).strip()
    if url and key:
        seed_rest(url, key, rows)
        return

    sys.exit(
        "set SUPABASE_DB_URL (or SUPABASE_URL + SUPABASE_SERVICE_KEY, "
        "see supabase/questions.sql)"
    )


if __name__ == "__main__":
    main()
