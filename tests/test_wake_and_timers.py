"""Phase 4 cost controls: wake channel + deadline-cached timer loop.

A free hosted Redis (Upstash: 500K commands/month) cannot afford a timer that
polls Redis 5x/second or an engine blocked on ``XREADGROUP`` (Upstash forbids
blocking reads anyway). These tests pin the behaviour that replaces polling:

* every action publishes a best-effort wake ping,
* the consumer sleeps on that ping instead of polling the stream,
* the timer loop touches Redis only on real transitions plus one slow rescan,
* ``ENGINE_BLOCK_MS=0`` translates to "no BLOCK argument" rather than
  ``BLOCK 0`` (which Redis reads as "block forever").
"""

from __future__ import annotations

import asyncio
import time

import fakeredis.aioredis as fr
import pytest

from engine import rules, store, timers
from engine.engine import RoundEngine, parse_block_ms
from shared import bus, keys
from shared.questions import load_json_bank
from shared.schemas import Phase


class CountingRedis:
    """Records the command names a component sends to Redis."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.calls: list[str] = []

    def __getattr__(self, name: str):
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        async def wrapper(*args, **kwargs):
            self.calls.append(name)
            return await attr(*args, **kwargs)

        return wrapper


async def _cancel(task: asyncio.Task) -> None:
    """Stop a background loop; tolerate it having already failed on its own."""
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        return
    except Exception:  # noqa: BLE001 - the failure was logged where it happened
        return


# --- wake channel -----------------------------------------------------------
async def test_send_action_publishes_wake_ping():
    redis = fr.FakeRedis()
    pubsub = redis.pubsub()
    await pubsub.subscribe(keys.WAKE)

    await bus.send_action(redis, {"kind": "sync", "conn_id": "c1"})

    message = None
    for _ in range(50):  # fakeredis' async pubsub does not block for the timeout
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.05)
        if message is not None:
            break
        await asyncio.sleep(0.02)
    assert message is not None, "send_action must ping the wake channel"
    channel = message["channel"]
    if isinstance(channel, bytes):
        channel = channel.decode("utf-8")
    assert channel == keys.WAKE
    assert await redis.xlen(keys.INGEST_STREAM) == 1
    await pubsub.aclose()


async def test_consumer_processes_action_via_wake(monkeypatch):
    """With polling disabled (30s safety net) an action still lands in <1s."""
    import engine.engine as engine_module

    monkeypatch.setattr(engine_module, "EMPTY_POLL_DELAY", 30.0)
    redis = fr.FakeRedis()
    engine = RoundEngine(redis, load_json_bank(), instance_id="eng-wake")
    await engine.start()
    wake_task = asyncio.create_task(engine.run_wake())
    consumer_task = asyncio.create_task(engine.run_consumer())
    try:
        await asyncio.sleep(0.1)  # let the Pub/Sub subscriber attach
        await bus.send_action(redis, {"kind": "sync", "conn_id": "c1", "code": "ZZZZZZ"})
        for _ in range(150):
            if engine.processed:
                break
            await asyncio.sleep(0.02)
        assert engine.processed >= 1, "consumer should wake on the action ping"
    finally:
        await engine.stop()
        await _cancel(consumer_task)
        await _cancel(wake_task)


# --- timer loop -------------------------------------------------------------
async def test_timer_loop_is_silent_when_nothing_is_due():
    """No deadline, no rooms changing: the loop must issue zero commands."""
    redis = fr.FakeRedis()
    lobby = rules.create_room("host", code="ABCDEF")
    await bus.save_room(redis, lobby)

    counter = CountingRedis(redis)
    task = asyncio.create_task(
        timers.run_timer_loop(
            counter,
            load_json_bank(),
            "eng-idle",
            tick_seconds=0.01,
            rescan_seconds=60.0,
            idle_seconds=60.0,
        )
    )
    try:
        await asyncio.sleep(0.3)
        # Boot rescan only: one SMEMBERS + one GET for the lobby room.
        assert counter.calls == ["smembers", "get"], counter.calls
    finally:
        await _cancel(task)


async def test_timer_loop_advances_at_its_deadline(questions):
    redis = fr.FakeRedis()
    state = rules.create_room("host", code="ABCDEF")
    for index in range(1, 4):
        rules.join_room(state, f"player{index}")
    rules.start_game(state, questions)  # QUESTION phase
    state.timer_ends_at = time.time() + 0.05
    await bus.save_room(redis, state)

    task = asyncio.create_task(
        timers.run_timer_loop(
            redis,
            questions,
            "eng-live",
            tick_seconds=0.01,
            rescan_seconds=60.0,
            idle_seconds=60.0,
            wake=asyncio.Event(),
        )
    )
    try:
        room = None
        for _ in range(150):
            room = await bus.load_room(redis, "ABCDEF")
            if room.phase is not Phase.QUESTION:
                break
            await asyncio.sleep(0.02)
        assert room is not None and room.phase is Phase.DISCUSSION
        assert room.timer_ends_at and room.timer_ends_at > time.time()
    finally:
        await _cancel(task)


async def test_engine_deadline_index_follows_mutations(questions):
    """Actions keep the embedded engine's cache fresh (no rescan needed)."""
    redis = fr.FakeRedis()
    engine = RoundEngine(redis, questions, instance_id="eng-index")
    await engine.start()
    try:
        # create -> the new room is observed immediately
        await engine.handle_action({"kind": "create_room", "conn_id": "c0", "nickname": "host"})
        codes = {c.upper() for c in await bus.active_room_codes(redis)}
        assert len(codes) == 1
        code = codes.pop()
        assert code in engine.deadlines.codes()

        # sole player leaves -> delete path clears the cache entry
        await engine.handle_action({"kind": "leave", "conn_id": "c0", "code": code})
        assert code not in engine.deadlines.codes()
        assert await bus.load_room(redis, code) is None

        # fresh room -> start a game -> _mutate observes the QUESTION deadline
        await engine.handle_action({"kind": "create_room", "conn_id": "c0", "nickname": "host"})
        codes = {c.upper() for c in await bus.active_room_codes(redis)}
        assert len(codes) == 1
        code = codes.pop()
        assert code in engine.deadlines.codes()
        for index in range(1, 4):
            await engine.handle_action(
                {
                    "kind": "join",
                    "conn_id": f"c{index}",
                    "code": code,
                    "nickname": f"p{index}",
                }
            )
        room = await bus.load_room(redis, code)
        host_id = room.players[0].id
        await engine.handle_action(
            {"kind": "host_start", "conn_id": "c0", "code": code, "player_id": host_id}
        )
        room = await bus.load_room(redis, code)
        assert room.phase is Phase.QUESTION
        assert engine.deadlines.next_deadline() == room.timer_ends_at
    finally:
        await engine.stop()


# --- advance_room outcomes --------------------------------------------------
async def test_advance_room_outcomes(questions):
    redis = fr.FakeRedis()
    state = rules.create_room("host", code="ABCDEF")
    for index in range(1, 4):
        rules.join_room(state, f"player{index}")
    rules.start_game(state, questions)
    state.timer_ends_at = time.time() + 60
    await bus.save_room(redis, state)

    # future deadline -> UNCHANGED (and no transition)
    outcome, room = await timers.advance_room(redis, questions, "eng", "ABCDEF", time.time())
    assert outcome is timers.AdvanceOutcome.UNCHANGED
    assert room.phase is Phase.QUESTION

    # expired deadline -> ADVANCED
    outcome, room = await timers.advance_room(
        redis, questions, "eng", "ABCDEF", state.timer_ends_at + 0.01
    )
    assert outcome is timers.AdvanceOutcome.ADVANCED
    assert room.phase is Phase.DISCUSSION

    # unknown room -> GONE (and it is scrubbed from the room set)
    outcome, room = await timers.advance_room(redis, questions, "eng", "ZZZZZZ", time.time())
    assert outcome is timers.AdvanceOutcome.GONE and room is None
    members = {
        m.decode() if isinstance(m, bytes) else m for m in await redis.smembers(keys.ROOMS_SET)
    }
    assert "ZZZZZZ" not in members

    # lock held elsewhere -> BUSY
    await store.acquire_lock(redis, "ABCDEF", "other-engine", wait_s=0)
    outcome, room = await timers.advance_room(redis, questions, "eng", "ABCDEF", time.time())
    assert outcome is timers.AdvanceOutcome.BUSY and room is None


async def test_deadline_index_rescan():
    redis = fr.FakeRedis()
    state = rules.create_room("host", code="ABCDEF")
    state.phase = Phase.QUESTION
    state.timer_ends_at = 100.0
    await bus.save_room(redis, state)

    index = timers.DeadlineIndex()
    assert await index.rescan(redis) == 1
    assert index.due(99.0) == []
    assert index.due(101.0) == ["ABCDEF"]
    assert index.next_deadline() == 100.0

    index.defer("ABCDEF", 102.0)
    assert index.due(101.0) == []

    await redis.delete(keys.room_state("ABCDEF"))
    await index.rescan(redis)
    assert index.codes() == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1000", 1000),
        ("0", None),
        ("-1", None),
        (None, 1000),
        ("bogus", 1000),
    ],
)
def test_parse_block_ms(raw, expected):
    assert parse_block_ms(raw) == expected
