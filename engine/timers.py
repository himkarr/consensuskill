"""Phase timers.

The engine is the only authority for time: it stamps ``timer_ends_at`` into the
room document, every client just counts down to that timestamp.

Two entry points:

* :func:`tick` — scan-based "advance every expired room once". Used by tests
  and as the hot path; it reads Redis on every call.
* :func:`run_timer_loop` — the live loop, tuned so a hosted Redis stays
  affordable (Upstash free = 500K commands/month). It keeps an in-memory
  :class:`DeadlineIndex` and only touches Redis when a deadline has actually
  passed; a slow rescan (default 30s, 60s while no rooms exist) reconciles
  that cache with Redis. The per-room SET NX lock still guarantees that only
  one engine replica ever performs a transition.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import os
import time
from collections.abc import Sequence
from typing import Any

from redis.asyncio import Redis

from engine import rules, store
from shared import bus, keys
from shared.schemas import Phase, Question, RoomState

logger = logging.getLogger(__name__)

TICK_SECONDS = float(os.environ.get("ENGINE_TICK_SECONDS", "0.2"))
RESCAN_SECONDS = float(os.environ.get("ENGINE_RESCAN_SECONDS", "30"))
IDLE_TICK_SECONDS = float(os.environ.get("ENGINE_IDLE_TICK_SECONDS", "60"))
BUSY_RETRY_SECONDS = float(os.environ.get("ENGINE_BUSY_RETRY_SECONDS", "0.5"))
SWEEP_SECONDS = float(os.environ.get("ENGINE_SWEEP_SECONDS", "300"))


class AdvanceOutcome(str, enum.Enum):
    """What happened to one room during an advance attempt."""

    ADVANCED = "advanced"  # phase changed and was saved
    UNCHANGED = "unchanged"  # deadline not reached (or nothing to advance)
    GONE = "gone"  # room document no longer exists
    BUSY = "busy"  # another holder had the lock this time


async def advance_room(
    redis: Redis,
    questions: Sequence[Question],
    instance_id: str,
    code: str,
    now: float,
) -> tuple[AdvanceOutcome, RoomState | None]:
    """Advance one room by exactly one phase.

    Returns the outcome plus the room document as it stands after the attempt
    (``None`` when it could not be read), so the caller can refresh its
    in-memory deadline cache without a second ``GET``.
    """
    if not await store.acquire_lock(redis, code, instance_id, ttl_ms=2_000, wait_s=0.5):
        return AdvanceOutcome.BUSY, None
    try:
        state = await bus.load_room(redis, code)
        if state is None:
            await redis.srem(keys.ROOMS_SET, code)
            return AdvanceOutcome.GONE, None
        if state.timer_ends_at is None or now < state.timer_ends_at:
            return AdvanceOutcome.UNCHANGED, state
        if state.phase in (Phase.LOBBY, Phase.GAME_OVER):
            return AdvanceOutcome.UNCHANGED, state

        previous_phase = state.phase
        rules.advance_on_timeout(state, questions, now=now)
        if state.phase == previous_phase:
            return AdvanceOutcome.UNCHANGED, state
        await bus.save_room(redis, state)
        await bus.broadcast(redis, code, rules.broadcast_state(state))
        logger.info(
            "room %s: %s -> %s (round %s)",
            code,
            previous_phase.value,
            state.phase.value,
            state.round_no,
        )
        return AdvanceOutcome.ADVANCED, state
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
            outcome, _ = await advance_room(redis, questions, instance_id, code, now)
            if outcome is AdvanceOutcome.ADVANCED:
                advanced.append(code)
        except Exception:  # noqa: BLE001 - one bad room must not stop the loop
            logger.exception("timer tick failed for room %s", code)
    return advanced


class DeadlineIndex:
    """In-memory ``room code -> timer_ends_at`` cache.

    Rooms are only written by the engine, so this stays correct through
    :meth:`observe`/``drop`` calls; :meth:`rescan` reconciles it with Redis
    (boot, other replicas, restored data).
    """

    def __init__(self) -> None:
        self._deadlines: dict[str, float | None] = {}
        self._retry_after: dict[str, float] = {}

    def observe(self, state: RoomState) -> None:
        code = state.code.upper()
        self._deadlines[code] = state.timer_ends_at
        self._retry_after.pop(code, None)

    def drop(self, code: str) -> None:
        code = code.upper()
        self._deadlines.pop(code, None)
        self._retry_after.pop(code, None)

    def defer(self, code: str, when: float) -> None:
        self._retry_after[code.upper()] = when

    def codes(self) -> list[str]:
        return list(self._deadlines)

    def due(self, now: float) -> list[str]:
        out = []
        for code, deadline in self._deadlines.items():
            if deadline is None or deadline > now:
                continue
            if self._retry_after.get(code, 0.0) > now:
                continue
            out.append(code)
        return out

    def next_deadline(self) -> float | None:
        pending = [d for d in self._deadlines.values() if d is not None]
        return min(pending) if pending else None

    async def rescan(self, redis: Redis) -> int:
        """Rebuild the cache from Redis. Returns the number of live rooms."""
        codes = {code.upper() for code in await bus.active_room_codes(redis)}
        for code in codes:
            state = await bus.load_room(redis, code)
            if state is None:
                self.drop(code)
            else:
                self.observe(state)
        for stale in set(self._deadlines) - codes:
            self.drop(stale)
        return len(codes)


async def _wait(wake: asyncio.Event | None, seconds: float) -> None:
    """Sleep, returning early when ``wake`` is set (or immediately at ``<= 0``)."""
    if seconds <= 0:
        if wake is not None:
            wake.clear()
        return
    if wake is None:
        await asyncio.sleep(seconds)
        return
    wake.clear()
    try:
        await asyncio.wait_for(wake.wait(), timeout=seconds)
    except TimeoutError:  # noqa: UP041 - asyncio.TimeoutError is TimeoutError on 3.11+
        pass


async def run_timer_loop(
    redis: Redis,
    questions: Sequence[Question],
    instance_id: str,
    *,
    deadlines: DeadlineIndex | None = None,
    wake: asyncio.Event | None = None,
    tick_seconds: float = TICK_SECONDS,
    rescan_seconds: float = RESCAN_SECONDS,
    idle_seconds: float = IDLE_TICK_SECONDS,
) -> None:
    """Advance rooms on their deadlines without polling Redis on every tick.

    Between deadlines the loop is pure CPU: it sleeps until the next cached
    deadline (or until ``wake`` fires, which the engine sets whenever it saves
    a room). Redis is touched only on real transitions plus one rescan per
    ``rescan_seconds`` (``idle_seconds`` while no room exists).
    """
    index = deadlines if deadlines is not None else DeadlineIndex()
    await index.rescan(redis)
    last_rescan = time.monotonic()
    logger.info(
        "timer loop started (rescan every %.0fs, idle %.0fs, wake %s)",
        rescan_seconds,
        idle_seconds,
        "on" if wake is not None else "off",
    )
    while True:
        now = time.time()
        interval = idle_seconds if not index.codes() else rescan_seconds
        if time.monotonic() - last_rescan >= interval:
            await index.rescan(redis)
            last_rescan = time.monotonic()

        due = index.due(now)
        for code in due:
            outcome, state = await advance_room(redis, questions, instance_id, code, now)
            if state is not None:
                index.observe(state)
            elif outcome is AdvanceOutcome.GONE:
                index.drop(code)
            else:  # BUSY: somebody else holds the lock; back off briefly
                index.defer(code, now + BUSY_RETRY_SECONDS)

        elapsed = time.monotonic() - last_rescan
        next_deadline = index.next_deadline()
        if due:
            sleep_for = tick_seconds  # a transition just happened; check again soon
        elif next_deadline is None:
            sleep_for = interval - elapsed  # lobby / no rooms: pure CPU wait
        elif next_deadline > now:
            sleep_for = next_deadline - now
        else:
            sleep_for = tick_seconds  # deferred by a BUSY lock: retry shortly

        # Never outlive the rescan cadence, and never busy-spin on it.
        await _wait(wake, max(0.0, min(sleep_for, interval - elapsed)))


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
    return {"tick_seconds": TICK_SECONDS, "rescan_seconds": RESCAN_SECONDS}
