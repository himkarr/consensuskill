"""Question bank loader.

Uses Supabase when ``SUPABASE_URL`` + ``SUPABASE_KEY`` are present, otherwise
falls back to the local ``data/questions.json`` bank. Any Supabase failure
(network, schema, empty table) falls back to the JSON bank so the game is
always playable.
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


def load_questions(force_reload: bool = False) -> list[Question]:
    global _CACHE
    with _LOCK:
        if _CACHE is not None and not force_reload:
            return list(_CACHE)

        questions: list[Question] | None = None
        url = os.environ.get("SUPABASE_URL", "").strip()
        key = os.environ.get("SUPABASE_KEY", "").strip()
        if url and key:
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
