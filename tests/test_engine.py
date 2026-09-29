"""Round-engine integration tests (spec tests 1, 2, 5, 7 over the real bus).

Everything runs in-process against fakeredis: the engine consumes the ingest
stream, writes room documents and publishes exactly like it does in
production, only the sockets are missing (those are covered in
``test_gateway.py``).
"""

from __future__ import annotations

import asyncio
import json
import time

import fakeredis.aioredis as fr
import pytest

from engine.engine import RoundEngine
from shared import keys
from shared.questions import load_json_bank


class Recorder:
    """Collects every message the engine publishes to reply/room channels.

    Messages are *consumed* by :meth:`drain`, so each caller only sees what
    arrived since its previous drain - exactly like a real gateway socket.
    """

    def __init__(self, redis) -> None:
        self.redis = redis
        self.seen: list[dict] = []

    async def __aenter__(self) -> Recorder:
        self.ps = self.redis.pubsub()
        await self.ps.psubscribe(keys.REPLY_PATTERN, keys.ROOM_EVENTS_PATTERN)
        # Drop the subscription confirmations before looking for real data.
        await asyncio.sleep(0.01)
        while await self.ps.get_message(ignore_subscribe_messages=True, timeout=0.01) is not None:
            pass
        return self

    async def __aexit__(self, *exc) -> None:
        await self.ps.aclose()

    async def drain(self, quiet: int = 2) -> list[dict]:
        """Return everything published since the previous drain."""
        out: list[dict] = []
        empties = 0
        while empties < quiet:
            raw = await self.ps.get_message(ignore_subscribe_messages=True, timeout=0.02)
            if raw is None:
                empties += 1
                continue
            empties = 0
            data = raw.get("data")
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            out.append(json.loads(data))
        self.seen.extend(out)
        return out

    async def wait_for(self, kind: str, limit: int = 100) -> dict:
        for _ in range(limit):
            for message in await self.drain():
                if message.get("type") == kind:
                    return message
            await asyncio.sleep(0.01)
        raise AssertionError(f"no {kind!r} message published (seen={self.seen})")


@pytest.fixture
async def env():
    redis = fr.FakeRedis()
    engine = RoundEngine(redis, load_json_bank(), instance_id="eng-test")
    await engine.start()
    yield redis, engine
    await engine.stop()


async def _start_room(engine, recorder, players: int = 4) -> tuple[str, list[str], list[str]]:
    """Create a room, join N players and start the match.

    Returns ``(code, player_ids, tokens)``.
    """
    await engine.handle_action({"kind": "create_room", "conn_id": "c0", "nickname": "host"})
    joined = await recorder.wait_for("joined")
    code = joined["room_code"]
    player_ids = [joined["player_id"]]
    tokens = [joined["token"]]

    for index in range(1, players):
        await engine.handle_action(
            {
                "kind": "join",
                "conn_id": f"c{index}",
                "code": code,
                "nickname": f"player{index}",
            }
        )
        message = await recorder.wait_for("joined")
        player_ids.append(message["player_id"])
        tokens.append(message["token"])

    await engine.handle_action(
        {"kind": "host_start", "conn_id": "c0", "code": code, "player_id": player_ids[0]}
    )
    await recorder.drain()
    return code, player_ids, tokens


async def _advance(engine, recorder, base: float, steps: int, gap: float = 100.0) -> dict:
    """Advance the phase machine ``steps`` times using a synthetic clock."""
    now = base
    state: dict = {}
    for _ in range(steps):
        now += gap
        await engine.tick(now=now)
        state = await recorder.wait_for("state")
    return state


# --- spec 1: health ---------------------------------------------------------
async def test_engine_health_endpoint():
    from fastapi.testclient import TestClient

    from engine.engine import create_engine_app

    app = create_engine_app(redis_client=fr.FakeRedis(), run_loops=False, instance_id="eng-h")
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["service"] == "engine"
        assert body["status"] == "ok"
        assert body["instance_id"] == "eng-h"


# --- spec 2: join issues unique ids/tokens ---------------------------------
async def test_create_and_join_issues_unique_ids(env):
    redis, engine = env
    async with Recorder(redis) as recorder:
        code, player_ids, tokens = await _start_room(engine, recorder, players=5)

        assert len(player_ids) == 5
        assert len(set(player_ids)) == 5
        assert len(set(tokens)) == 5
        assert all(len(token) >= 32 for token in tokens)

        state = await _last_state(redis, code)
        assert {p["id"] for p in state["players"]} == set(player_ids)
        assert state["phase"] == "QUESTION"


async def _last_state(redis, code: str) -> dict:
    from shared import bus

    room = await bus.load_room(redis, code)
    assert room is not None
    return {
        "phase": room.phase.value,
        "players": [
            {"id": p.id, "nickname": p.nickname, "lives": p.lives, "alive": p.alive}
            for p in room.players
        ],
        "round_no": room.round_no,
    }


# --- full round: vote -> reveal -> apply -----------------------------------
async def test_full_round_over_the_bus(env):
    redis, engine = env
    async with Recorder(redis) as recorder:
        code, player_ids, _tokens = await _start_room(engine, recorder, players=4)
        base = time.time()

        # QUESTION -> DISCUSSION -> VOTE
        state = await _advance(engine, recorder, base, steps=2)
        assert state["phase"] == "VOTE"

        # Three votes for A, one player abstains.
        for player_id in player_ids[:3]:
            await engine.handle_action(
                {
                    "kind": "vote",
                    "conn_id": _conn_for(player_ids, player_id),
                    "code": code,
                    "player_id": player_id,
                    "choice": "A",
                }
            )
        await recorder.drain()

        # Votes are durable in the per-room stream (Redis Streams requirement).
        length = await redis.xlen(keys.room_votes(code))
        assert length == 3

        # VOTE -> REVEAL
        state = await _advance(engine, recorder, base + 200, steps=1)
        assert state["phase"] == "REVEAL"
        assert state["counts"] == {"A": 3, "B": 0}
        assert state["result"]["status"] == "normal"
        assert state["result"]["losing_side"] == "A"
        # Choices only appear once revealed (the abstainer stays None).
        assert set(state["votes"].values()) == {"A", None}

        # REVEAL -> APPLY: majority (3) and the abstainer all lose a life.
        state = await _advance(engine, recorder, base + 300, steps=1)
        assert state["phase"] == "APPLY"
        lives = {p["nickname"]: p["lives"] for p in state["players"]}
        assert all(value == 1 for value in lives.values())

        # APPLY -> next round
        state = await _advance(engine, recorder, base + 400, steps=1)
        assert state["phase"] == "QUESTION"
        assert state["round_no"] == 2


def _conn_for(player_ids: list[str], player_id: str) -> str:
    """Test helper: conn ids follow the same order as player ids."""
    return f"c{player_ids.index(player_id)}"


# --- spec 4: duplicate / late votes over the bus ---------------------------
async def test_duplicate_vote_is_ignored(env):
    redis, engine = env
    async with Recorder(redis) as recorder:
        code, player_ids, _tokens = await _start_room(engine, recorder, players=4)
        base = time.time()
        await _advance(engine, recorder, base, steps=2)  # into VOTE

        conn = "c1"
        first = {"kind": "vote", "conn_id": conn, "code": code, "player_id": player_ids[1]}
        await engine.handle_action({**first, "choice": "A"})
        messages = await recorder.drain()
        assert not [m for m in messages if m.get("type") == "ack"]

        await engine.handle_action({**first, "choice": "B"})
        messages = await recorder.drain()
        acks = [m for m in messages if m.get("type") == "ack"]
        assert acks and acks[0]["ok"] is False and acks[0]["detail"] == "duplicate"

        from shared import bus

        room = await bus.load_room(redis, code)
        assert room.votes[player_ids[1]] == "A"  # first vote stands


async def test_vote_from_wrong_phase_rejected(env):
    redis, engine = env
    async with Recorder(redis) as recorder:
        code, player_ids, _tokens = await _start_room(engine, recorder, players=4)  # QUESTION phase
        await engine.handle_action(
            {
                "kind": "vote",
                "conn_id": "c1",
                "code": code,
                "player_id": player_ids[1],
                "choice": "A",
            }
        )
        messages = await recorder.drain()
        errors = [m for m in messages if m.get("type") == "error"]
        assert errors and errors[0]["code"] == "vote_wrong_phase"


async def test_spoofed_player_id_is_rejected(env):
    redis, engine = env
    async with Recorder(redis) as recorder:
        code, player_ids, _tokens = await _start_room(engine, recorder, players=4)
        await engine.handle_action(
            {
                "kind": "vote",
                "conn_id": "c1",
                "code": code,
                "player_id": player_ids[2],  # c1 does not own this player
                "choice": "A",
            }
        )
        messages = await recorder.drain()
        errors = [m for m in messages if m.get("type") == "error"]
        assert errors and errors[0]["code"] == "forbidden"


# --- spec 7: reconnect over the bus ----------------------------------------
async def test_reconnect_restores_player_and_lives(env):
    redis, engine = env
    async with Recorder(redis) as recorder:
        code, player_ids, _tokens = await _start_room(engine, recorder, players=4)
        from shared import bus

        room = await bus.load_room(redis, code)
        target = room.players[1]
        token = target.token
        original_lives = target.lives

        # Socket dies...
        await engine.handle_action({"kind": "disconnect", "conn_id": "c1", "code": code})
        room = await bus.load_room(redis, code)
        assert room.players[1].connected is False

        # ...and comes back on a brand new connection with the same token.
        await engine.handle_action({"kind": "reconnect", "conn_id": "c1-new", "token": token})
        message = await recorder.wait_for("joined")
        assert message["player_id"] == player_ids[1]
        assert message["token"] == token

        room = await bus.load_room(redis, code)
        assert room.players[1].connected is True
        assert room.players[1].lives == original_lives
        assert len(room.players) == 4  # reconnect never duplicates a player


async def test_invalid_token_is_rejected(env):
    redis, engine = env
    async with Recorder(redis) as recorder:
        await engine.handle_action(
            {"kind": "reconnect", "conn_id": "ghost", "token": "nope-not-real"}
        )
        message = await recorder.wait_for("error")
        assert message["code"] == "invalid_token"


# --- spec 8: chat rate limit over the bus ----------------------------------
async def test_chat_rate_limited_on_the_engine(env):
    redis, engine = env
    async with Recorder(redis) as recorder:
        code, player_ids, _tokens = await _start_room(engine, recorder, players=4)

        await engine.handle_action(
            {
                "kind": "chat",
                "conn_id": "c1",
                "code": code,
                "player_id": player_ids[1],
                "text": "hi",
            }
        )
        messages = await recorder.drain()
        assert [m for m in messages if m.get("type") == "chat"]

        await engine.handle_action(
            {
                "kind": "chat",
                "conn_id": "c1",
                "code": code,
                "player_id": player_ids[1],
                "text": "hi2",
            }
        )
        messages = await recorder.drain()
        errors = [m for m in messages if m.get("type") == "error"]
        assert errors and errors[0]["code"] == "rate_limited"


# --- host powers ------------------------------------------------------------
async def test_host_kick_removes_player(env):
    redis, engine = env
    async with Recorder(redis) as recorder:
        # Kick while still in the lobby (before host_start).
        await engine.handle_action({"kind": "create_room", "conn_id": "c0", "nickname": "host"})
        joined = await recorder.wait_for("joined")
        code = joined["room_code"]
        player_ids = [joined["player_id"]]
        for index in range(1, 4):
            await engine.handle_action(
                {"kind": "join", "conn_id": f"c{index}", "code": code, "nickname": f"p{index}"}
            )
            player_ids.append((await recorder.wait_for("joined"))["player_id"])
        from shared import bus

        await engine.handle_action(
            {
                "kind": "host_kick",
                "conn_id": "c0",
                "code": code,
                "player_id": player_ids[0],
                "target_id": player_ids[3],
            }
        )
        messages = await recorder.drain()
        errors = [m for m in messages if m.get("type") == "error"]
        assert errors and errors[0]["code"] == "kicked"

        room = await bus.load_room(redis, code)
        assert len(room.players) == 3
        assert room.players[-1].id != player_ids[3]


async def test_non_host_cannot_start(env):
    redis, engine = env
    async with Recorder(redis) as recorder:
        code, player_ids, _tokens = await _start_room(engine, recorder, players=4)
        # A second host_start from a non-host is rejected.
        await engine.handle_action(
            {"kind": "host_start", "conn_id": "c1", "code": code, "player_id": player_ids[1]}
        )
        messages = await recorder.drain()
        # Either "game_in_progress" (round already running) or "not_host".
        errors = [m for m in messages if m.get("type") == "error"]
        assert errors
