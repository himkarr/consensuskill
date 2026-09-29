"""Phase timers.

The engine is the only authority for time: it stamps ``timer_ends_at`` into the
room document, every client just counts down to that timestamp. A short poll
loop advances rooms whose deadline passed; the per-room Redis lock guarantees
that only one engine replica ever performs the transition.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Sequence
from typing import Any

from redis.asyncio import Redis

from engine import rules, store
from shared import bus, keys
from shared.schemas import Phase, Question

logger = logging.getLogger(__name__)

TICK_SECONDS = float(os.environ.get("ENGINE_TICK_SECONDS", "0.2"))
SWEEP_SECONDS = float(os.environ.get("ENGINE_SWEEP_SECONDS", "60"))


async def advance_room(
    redis: Redis,
    questions: Sequence[Question],
    instance_id: str,
    code: str,
    now: float,
) -> bool:
    """Advance one room by exactly one phase. Returns True if it changed."""
    if not await store.acquire_lock(redis, code, instance_id, ttl_ms=2_000, wait_s=0.5):
        return False
    try:
        state = await bus.load_room(redis, code)
        if state is None:
            await redis.srem(keys.ROOMS_SET, code)
            return False
        if state.timer_ends_at is None or now < state.timer_ends_at:
            return False
        if state.phase in (Phase.LOBBY, Phase.GAME_OVER):
            return False

        previous_phase = state.phase
        rules.advance_on_timeout(state, questions, now=now)
        if state.phase == previous_phase:
            return False
        await bus.save_room(redis, state)
        await bus.broadcast(redis, code, rules.broadcast_state(state))
        logger.info(
            "room %s: %s -> %s (round %s)",
            code,
            previous_phase.value,
            state.phase.value,
            state.round_no,
        )
        return True
    finally:
        await store.release_lock(redis, code, instance_id)


async def tick(
    redis: Redis,
    questions: Sequence[Question],
    instance_id: str,
    now: float | None = None,
) -> list[str]:
    """Advance every room whose deadline has passed. Returns advanced codes."""
    now = time.time() if now is None else now
    advanced: list[str] = []
    for code in await bus.active_room_codes(redis):
        try:
            if await advance_room(redis, questions, instance_id, code, now):
                advanced.append(code)
        except Exception:  # noqa: BLE001 - one bad room must not stop the loop
            logger.exception("timer tick failed for room %s", code)
    return advanced


async def run_timer_loop(
    redis: Redis,
    questions: Sequence[Question],
    instance_id: str,
    *,
    tick_seconds: float = TICK_SECONDS,
) -> None:
    logger.info("timer loop started (every %.2fs)", tick_seconds)
    while True:
        started = time.monotonic()
        await tick(redis, questions, instance_id)
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(0.0, tick_seconds - elapsed))


async def sweep_empty_rooms(redis: Redis) -> list[str]:
    """Drop rooms that lost every player (people leaving a lobby)."""
    removed: list[str] = []
    for code in await bus.active_room_codes(redis):
        state = await bus.load_room(redis, code)
        if state is None or state.players:
            continue
        await bus.delete_room(redis, state)
        removed.append(code)
    return removed


async def run_sweep_loop(redis: Redis, *, sweep_seconds: float = SWEEP_SECONDS) -> None:
    while True:
        await asyncio.sleep(sweep_seconds)
        try:
            for code in await sweep_empty_rooms(redis):
                logger.info("swept empty room %s", code)
        except Exception:  # noqa: BLE001
            logger.exception("room sweep failed")


def describe(redis: Redis) -> dict[str, Any]:  # pragma: no cover - diagnostics
    return {"tick_seconds": TICK_SECONDS}
