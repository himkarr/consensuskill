"""The round engine: the single authority for game state.

Gateways are stateless sockets; everything a room *is* lives in one JSON
document in Redis. This service:

* consumes client actions from a Redis Stream (consumer group, crash-safe),
* applies the pure rules from :mod:`engine.rules`,
* owns every timer (phase transitions) behind a per-room SET NX lock,
* publishes state/chat events to a per-room Pub/Sub channel that all
  gateways forward to their local sockets.

Run it with: ``uvicorn engine.engine:app --port 8001``
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from redis.asyncio import Redis
from redis.exceptions import ResponseError as RedisResponseError

from engine import rules, store, timers
from shared import bus, keys
from shared.errors import GameError
from shared.questions import load_questions
from shared.schemas import (
    Phase,
    Player,
    RoomState,
    ack_message,
    chat_message,
    error_message,
    joined_message,
)

logger = logging.getLogger(__name__)

DEFAULT_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
BLOCK_MS = os.environ.get("ENGINE_BLOCK_MS", "1000")
CONSUMER_BATCH = int(os.environ.get("ENGINE_BATCH", "20"))
EMPTY_POLL_DELAY = float(os.environ.get("ENGINE_EMPTY_POLL_DELAY", "0.05"))


def parse_block_ms(raw: str | int | None, default: int = 1000) -> int | None:
    """XREADGROUP's ``BLOCK`` argument: ``0``/negative means "no blocking" in
    our config, but Redis rejects ``BLOCK 0`` (it means "block forever"), so we
    translate to ``None`` = omit the argument entirely. Hosted Redis (Upstash)
    disallows blocking reads, so deploy sets ``ENGINE_BLOCK_MS=0`` and the
    consumer waits on the wake channel instead.
    """
    if raw is None:
        raw = default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return None if value <= 0 else value


def default_instance_id() -> str:
    return os.environ.get("INSTANCE_ID") or f"engine-{uuid.uuid4().hex[:8]}"


def sync_connected(state: RoomState) -> None:
    """Recompute ``player.connected`` from the live socket map."""
    active = set(state.conns.values())
    for player in state.players:
        player.connected = player.id in active


class RoundEngine:
    def __init__(
        self,
        redis: Redis,
        questions: list,
        instance_id: str | None = None,
    ) -> None:
        self.redis = redis
        self.questions = questions
        self.instance_id = instance_id or default_instance_id()
        self.group = keys.INGEST_GROUP
        self.consumer = f"{self.instance_id}-{uuid.uuid4().hex[:6]}"
        self.started_at = time.time()
        self.processed = 0
        self._stopping = False
        # wake signals: the consumer sleeps on `wake` between XREADGROUPs and
        # the timer loop sleeps on `timer_wake` between deadlines, so neither
        # polls Redis when there is nothing to do (hosted Redis = 500K cmds/month).
        self.wake = asyncio.Event()
        self.timer_wake = asyncio.Event()
        self.deadlines = timers.DeadlineIndex()

    # ------------------------------------------------------------------ setup
    async def start(self) -> None:
        try:
            await self.redis.xgroup_create(
                keys.INGEST_STREAM, self.group, id="0", mkstream=True
            )
            logger.info("created consumer group %s", self.group)
        except RedisResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        await self.reclaim_pending()

    async def reclaim_pending(self, min_idle_ms: int = 30_000) -> int:
        """Re-read actions that a crashed engine never acknowledged."""
        claimed = 0
        try:
            start_id = "0-0"
            for _ in range(20):
                result = await self.redis.xautoclaim(
                    keys.INGEST_STREAM,
                    self.group,
                    self.consumer,
                    min_idle_time=min_idle_ms,
                    start_id=start_id,
                )
                messages = result[1] if isinstance(result, list | tuple) else []
                if not messages:
                    break
                for message_id, fields in messages:
                    await self._process(message_id, fields)
                    claimed += 1
                start_id = result[0] if result else "0-0"
        except Exception:  # noqa: BLE001 - older Redis may not support XAUTOCLAIM
            logger.debug("xautoclaim not available", exc_info=True)
        return claimed

    # ------------------------------------------------------------- consumer
    async def run_wake(self) -> None:
        """Subscribe to the wake channel the gateway pings on every action.

        Best-effort: if Pub/Sub is unavailable the task exits and the consumer
        falls back to its safety-net poll (``ENGINE_EMPTY_POLL_DELAY``).
        """
        try:
            pubsub = self.redis.pubsub()
            await pubsub.subscribe(keys.WAKE)
            logger.info("wake subscriber on %s", keys.WAKE)
            async for message in pubsub.listen():
                if self._stopping:
                    break
                if message and message.get("type") == "message":
                    self.wake.set()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - wake is an optimisation, never fatal
            logger.warning("wake subscriber failed; falling back to polling", exc_info=True)

    async def run_consumer(self) -> None:
        logger.info("ingest consumer started (%s)", self.consumer)
        block = parse_block_ms(BLOCK_MS)
        while not self._stopping:
            # Clear *before* reading: anything arriving later re-sets the event
            # and pulls us straight back out of the wait below.
            self.wake.clear()
            try:
                response = await self.redis.xreadgroup(
                    self.group,
                    self.consumer,
                    {keys.INGEST_STREAM: ">"},
                    count=CONSUMER_BATCH,
                    block=block,
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - reconnect on transient errors
                logger.exception("xreadgroup failed")
                await asyncio.sleep(0.5)
                continue

            if response:
                for _stream, messages in response:
                    for message_id, fields in messages:
                        await self._process(message_id, fields)
                continue

            # Empty read (fakeredis ignores BLOCK and returns immediately;
            # Upstash forbids blocking reads): wait for the wake ping, with a
            # safety-net timeout in case a publish was missed.
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=EMPTY_POLL_DELAY)
            except TimeoutError:  # noqa: UP041
                pass

    async def _process(self, message_id: Any, fields: dict) -> None:
        try:
            raw = fields.get(b"payload") or fields.get("payload")
            if raw:
                action = _loads(raw)
                await self.handle_action(action)
                self.processed += 1
        except Exception:  # noqa: BLE001 - never let one action kill the loop
            logger.exception("failed to process ingest message %s", message_id)
        finally:
            try:
                await self.redis.xack(keys.INGEST_STREAM, self.group, message_id)
            except Exception:  # noqa: BLE001
                logger.exception("xack failed for %s", message_id)

    # -------------------------------------------------------------- actions
    async def handle_action(self, action: dict[str, Any]) -> None:
        kind = action.get("kind")
        conn_id = action.get("conn_id", "")
        handler = _ACTION_HANDLERS.get(kind)
        if handler is None:
            await bus.reply(
                self.redis,
                conn_id,
                error_message("unknown_action", f"Unknown action: {kind}", conn_id),
            )
            return
        try:
            await handler(self, action, conn_id)
        except GameError as exc:
            await bus.reply(
                self.redis, conn_id, error_message(exc.code, exc.message, conn_id)
            )
        except Exception:  # noqa: BLE001
            logger.exception("action %s failed", kind)
            await bus.reply(
                self.redis,
                conn_id,
                error_message("internal", "Something went wrong on the server", conn_id),
            )

    async def _mutate(self, code: str, fn):
        """Lock -> load -> apply pure rules -> save. Raises GameError on failure."""
        code = (code or "").upper()
        if not await store.acquire_lock(self.redis, code, self.instance_id):
            raise GameError("room_busy", "Room is busy, please try again")
        try:
            state = await bus.load_room(self.redis, code)
            if state is None:
                raise GameError("room_not_found", "Room not found or game already ended")
            result = fn(state)
            await bus.save_room(self.redis, state)
            self._observe_deadline(state)
            return state, result
        finally:
            await store.release_lock(self.redis, code, self.instance_id)

    @staticmethod
    def _player(state: RoomState, conn_id: str, claimed: str | None = None) -> Player:
        player_id = state.conns.get(conn_id)
        if player_id is None:
            raise GameError("not_joined", "Join a room first")
        if claimed and claimed != player_id:
            raise GameError("forbidden", "Player id mismatch")
        player = state.player_by_id(player_id)
        if player is None:
            raise GameError("not_joined", "You are no longer in this room")
        return player

    async def _broadcast(self, state: RoomState) -> None:
        await bus.broadcast(self.redis, state.code, rules.broadcast_state(state))

    def _observe_deadline(self, state: RoomState) -> None:
        """Keep the timer loop's in-memory deadline cache in sync and nudge it."""
        self.deadlines.observe(state)
        self.timer_wake.set()

    def _forget_deadline(self, code: str) -> None:
        self.deadlines.drop(code)
        self.timer_wake.set()

    # -- create / join / reconnect -----------------------------------------
    async def _on_create_room(self, action: dict, conn_id: str) -> None:
        nickname = action.get("nickname", "")
        state: RoomState | None = None
        for _ in range(8):
            candidate = rules.create_room(nickname)
            claimed = await self.redis.set(
                keys.room_state(candidate.code),
                candidate.model_dump_json(),
                nx=True,
            )
            if claimed:
                state = candidate
                break
        if state is None:
            raise GameError("code_exhausted", "Could not allocate a unique room code")

        state.conns[conn_id] = state.host_id
        sync_connected(state)
        await bus.save_room(self.redis, state, new_tokens=[state.players[0].token])
        self._observe_deadline(state)
        await bus.reply(
            self.redis,
            conn_id,
            joined_message(
                conn_id,
                state.players[0].id,
                state.players[0].token,
                state.code,
                is_host=True,
            ),
        )
        await self._broadcast(state)

    async def _on_join(self, action: dict, conn_id: str) -> None:
        code = (action.get("code") or "").upper().strip()
        nickname = action.get("nickname", "")
        if not code:
            raise GameError("invalid_room", "Room code is required")

        created: list[str] = []

        def apply(state: RoomState) -> Player:
            existing_id = state.conns.get(conn_id)
            if existing_id:
                player = state.player_by_id(existing_id)
                if player is not None:
                    return player  # idempotent: re-joining sends the same player
            player = rules.join_room(state, nickname)
            state.conns[conn_id] = player.id
            sync_connected(state)
            created.append(player.token)
            return player

        state, player = await self._mutate(code, apply)
        if created:
            await bus.index_token(self.redis, created[0], state.code)
        await bus.reply(
            self.redis,
            conn_id,
            joined_message(
                conn_id, player.id, player.token, state.code, is_host=player.id == state.host_id
            ),
        )
        await self._broadcast(state)

    async def _on_reconnect(self, action: dict, conn_id: str) -> None:
        token = action.get("token") or ""
        code = await bus.room_code_for_token(self.redis, token)
        if not code:
            raise GameError("invalid_token", "Unknown or expired player token")

        def apply(state: RoomState) -> Player:
            player = rules.handle_reconnect(state, token)
            state.conns[conn_id] = player.id
            sync_connected(state)
            return player

        state, player = await self._mutate(code, apply)
        await bus.reply(
            self.redis,
            conn_id,
            joined_message(
                conn_id, player.id, player.token, state.code, is_host=player.id == state.host_id
            ),
        )
        await self._broadcast(state)

    # -- chat / vote --------------------------------------------------------
    async def _on_chat(self, action: dict, conn_id: str) -> None:
        code = action.get("code") or ""
        text = action.get("text") or ""

        def apply(state: RoomState) -> object:
            player = self._player(state, conn_id, action.get("player_id"))
            if not rules.can_chat(state):
                raise GameError("chat_disabled", "Chat is disabled while voting")
            if not rules.chat_allowed(state, player.id):
                raise GameError("rate_limited", "Slow down: 1 message per second")
            return rules.append_chat(state, player, text)

        state, message = await self._mutate(code, apply)
        await bus.broadcast(self.redis, state.code, chat_message(message.model_dump()))

    async def _on_vote(self, action: dict, conn_id: str) -> None:
        code = action.get("code") or ""
        choice = action.get("choice")

        def apply(state: RoomState) -> object:
            player = self._player(state, conn_id, action.get("player_id"))
            outcome = rules.submit_vote(state, player.id, choice)
            return player, outcome

        state, (player, outcome) = await self._mutate(code, apply)
        if outcome.accepted:
            await store.audit_vote(
                self.redis,
                state.code,
                player_id=player.id,
                choice=str(choice),
                round_no=state.round_no,
            )
            await self._broadcast(state)
            return

        if outcome.reason in {"duplicate", "too_late"}:
            # Silently ignored by design: the first vote per round is the only
            # one that counts. The client gets an ack, not an error.
            await bus.reply(
                self.redis,
                conn_id,
                ack_message("vote", False, outcome.reason, conn_id),
            )
            return
        await bus.reply(
            self.redis,
            conn_id,
            error_message(f"vote_{outcome.reason}", f"Vote rejected: {outcome.reason}", conn_id),
        )

    # -- host ---------------------------------------------------------------
    async def _on_host_start(self, action: dict, conn_id: str) -> None:
        code = action.get("code") or ""

        def apply(state: RoomState) -> RoomState:
            player = self._player(state, conn_id, action.get("player_id"))
            if player.id != state.host_id:
                raise GameError("not_host", "Only the host can start the game")
            if state.phase not in (Phase.LOBBY, Phase.GAME_OVER):
                raise GameError("game_in_progress", "A round is already running")
            return rules.start_game(state, self.questions)

        state, _ = await self._mutate(code, apply)
        await self._broadcast(state)

    async def _on_host_kick(self, action: dict, conn_id: str) -> None:
        code = action.get("code") or ""
        target_id = action.get("target_id") or ""
        kicked_conns: list[str] = []

        def apply(state: RoomState) -> Player:
            actor = self._player(state, conn_id, action.get("player_id"))
            if state.phase not in (Phase.LOBBY, Phase.GAME_OVER):
                raise GameError("wrong_phase", "Players can only be kicked in the lobby")
            target = rules.host_kick(state, actor.id, target_id)
            for cid, pid in list(state.conns.items()):
                if pid == target.id:
                    kicked_conns.append(cid)
                    del state.conns[cid]
            sync_connected(state)
            return target

        state, target = await self._mutate(code, apply)
        for cid in kicked_conns:
            await bus.reply(
                self.redis,
                cid,
                error_message("kicked", f"You were kicked by {target.nickname}", cid),
            )
        await self._broadcast(state)

    # -- lifecycle ----------------------------------------------------------
    async def _on_disconnect(self, action: dict, conn_id: str) -> None:
        code = action.get("code") or ""
        if not code:
            return

        def apply(state: RoomState) -> bool:
            removed = state.conns.pop(conn_id, None) is not None
            sync_connected(state)
            return removed

        try:
            state, removed = await self._mutate(code, apply)
        except GameError:
            return  # room already gone
        if removed:
            await self._broadcast(state)

    async def _on_leave(self, action: dict, conn_id: str) -> None:
        code = action.get("code") or ""
        if not code:
            return

        def apply(state: RoomState) -> tuple[Player | None, bool]:
            player_id = state.conns.pop(conn_id, None)
            if player_id is None:
                return None, False
            player = state.player_by_id(player_id)
            if state.phase is Phase.LOBBY and player is not None:
                state.players.remove(player)
                if state.host_id == player.id and state.players:
                    state.host_id = state.players[0].id
                sync_connected(state)
                return player, True
            sync_connected(state)
            return player, False

        try:
            state, (player, removed) = await self._mutate(code, apply)
        except GameError:
            return
        if removed and player is not None:
            await self.redis.delete(keys.token_index(player.token))
        if not state.players:
            await bus.delete_room(self.redis, state)
            self._forget_deadline(state.code)
            return
        await self._broadcast(state)

    async def _on_sync(self, action: dict, conn_id: str) -> None:
        code = action.get("code") or ""
        state = await bus.load_room(self.redis, code.upper())
        if state is None:
            raise GameError("room_not_found", "Room not found or game already ended")
        await bus.reply(self.redis, conn_id, rules.broadcast_state(state))

    # -- background loops ---------------------------------------------------
    async def run_timer(self) -> None:
        await timers.run_timer_loop(
            self.redis,
            self.questions,
            self.instance_id,
            deadlines=self.deadlines,
            wake=self.timer_wake,
        )

    async def run_sweeper(self) -> None:
        await timers.run_sweep_loop(self.redis)

    async def stop(self) -> None:
        self._stopping = True

    async def tick(self, now: float | None = None) -> list[str]:
        """Test/hot-path entry point: advance every expired room once."""
        return await timers.tick(self.redis, self.questions, self.instance_id, now=now)


_ACTION_HANDLERS = {
    "create_room": RoundEngine._on_create_room,
    "join": RoundEngine._on_join,
    "reconnect": RoundEngine._on_reconnect,
    "chat": RoundEngine._on_chat,
    "vote": RoundEngine._on_vote,
    "host_start": RoundEngine._on_host_start,
    "host_kick": RoundEngine._on_host_kick,
    "disconnect": RoundEngine._on_disconnect,
    "leave": RoundEngine._on_leave,
    "sync": RoundEngine._on_sync,
}


def _loads(raw: Any) -> dict:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw)


def create_engine_app(
    redis_client: Redis | None = None,
    questions: list | None = None,
    instance_id: str | None = None,
    run_loops: bool = True,
) -> FastAPI:
    """FastAPI wrapper exposing ``/health`` and running the engine loops."""
    instance = instance_id or default_instance_id()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        redis = redis_client or Redis.from_url(DEFAULT_REDIS_URL, decode_responses=False)
        owns_redis = redis_client is None
        engine = RoundEngine(redis, questions or load_questions(), instance)
        app.state.redis = redis
        app.state.engine = engine
        tasks: list[asyncio.Task] = []
        if run_loops:
            await engine.start()
            tasks = [
                asyncio.create_task(engine.run_consumer(), name="ingest-consumer"),
                asyncio.create_task(engine.run_wake(), name="wake-subscriber"),
                asyncio.create_task(engine.run_timer(), name="phase-timer"),
                asyncio.create_task(engine.run_sweeper(), name="room-sweeper"),
            ]
            app.state.tasks = tasks
        try:
            yield
        finally:
            await engine.stop()
            for task in tasks:
                task.cancel()
            for task in tasks:
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            if owns_redis:
                await redis.aclose()

    app = FastAPI(title="ConsensusKill Round Engine", lifespan=lifespan)
    app.state.instance_id = instance

    @app.get("/health")
    async def health() -> dict[str, Any]:
        engine: RoundEngine | None = getattr(app.state, "engine", None)
        return {
            "service": "engine",
            "status": "ok",
            "instance_id": instance,
            "processed": engine.processed if engine else 0,
            "questions": len(engine.questions) if engine else 0,
            "time": time.time(),
        }

    return app


app = create_engine_app()
