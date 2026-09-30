"""Question bank loader.

Resolution order:

1. ``SUPABASE_DB_URL`` - direct Postgres connection (psycopg, lazy import).
2. ``SUPABASE_URL`` + ``SUPABASE_KEY`` - Supabase REST API.
3. ``data/questions.json`` - bundled fallback.

Any failure at an earlier tier falls back to the next, so the game is
always playable even when Supabase is unreachable.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

import httpx

from shared.schemas import Question

logger = logging.getLogger(__name__)

DEFAULT_BANK_PATH = Path(__file__).resolve().parent.parent / "data" / "questions.json"
_CACHE: list[Question] | None = None
_LOCK = threading.Lock()


def load_json_bank(path: Path | None = None) -> list[Question]:
    bank_path = path or DEFAULT_BANK_PATH
    with bank_path.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    items = raw["questions"] if isinstance(raw, dict) else raw
    return [Question.model_validate(item) for item in items]


def load_supabase_questions(url: str, key: str, timeout: float = 3.0) -> list[Question]:
    """Fetch the ``questions`` table through the Supabase REST API."""
    response = httpx.get(
        f"{url.rstrip('/')}/rest/v1/questions",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        params={"select": "id,text,option_a,option_b", "order": "id.asc"},
        timeout=timeout,
    )
    response.raise_for_status()
    rows = response.json()
    if not rows:
        raise ValueError("supabase returned no questions")
    return [Question.model_validate(row) for row in rows]


def load_supabase_db_questions(dsn: str, timeout: float = 3.0) -> list[Question]:
    """Fetch the ``questions`` table straight from Postgres (``SUPABASE_DB_URL``)."""
    import psycopg  # lazy: only needed when SUPABASE_DB_URL is configured

    # prepare_threshold=None: prepared statements break on Supabase's
    # transaction pooler (pgbouncer), which is what IPv4-only hosts use.
    with psycopg.connect(
        dsn, connect_timeout=max(1, int(timeout)), prepare_threshold=None
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("select id, text, option_a, option_b from public.questions order by id")
            rows = cur.fetchall()
    if not rows:
        raise ValueError("supabase returned no questions")
    return [
        Question.model_validate(
            {"id": r[0], "text": r[1], "option_a": r[2], "option_b": r[3]}
        )
        for r in rows
    ]


def load_questions(force_reload: bool = False) -> list[Question]:
    global _CACHE
    with _LOCK:
        if _CACHE is not None and not force_reload:
            return list(_CACHE)

        questions: list[Question] | None = None
        db_url = os.environ.get("SUPABASE_DB_URL", "").strip()
        if db_url:
            try:
                questions = load_supabase_db_questions(db_url)
                logger.info("Loaded %d questions from Supabase Postgres", len(questions))
            except Exception as exc:  # noqa: BLE001 - any failure falls back
                logger.warning("Supabase DB question load failed (%s); falling back", exc)

        url = os.environ.get("SUPABASE_URL", "").strip()
        key = os.environ.get("SUPABASE_KEY", "").strip()
        if not questions and url and key:
            try:
                questions = load_supabase_questions(url, key)
                logger.info("Loaded %d questions from Supabase", len(questions))
            except Exception as exc:  # noqa: BLE001 - any failure falls back
                logger.warning("Supabase question load failed (%s); using JSON bank", exc)

        if not questions:
            questions = load_json_bank()
            logger.info("Loaded %d questions from local JSON bank", len(questions))

        _CACHE = questions
        return list(_CACHE)
