"""Async Redis message-bus helpers shared by the gateway and the engine.

Transport only: gateways publish *actions*, the engine publishes *events*.
Nothing in here knows about game rules.
"""

from __future__ import annotations

import json
from typing import Any

from redis.asyncio import Redis

from shared import keys
from shared.schemas import RoomState


def encode(message: dict[str, Any]) -> str:
    return json.dumps(message, separators=(",", ":"), default=str)


async def send_action(redis: Redis, action: dict[str, Any]) -> None:
    """Publish a client action to the single ingest stream."""
    await redis.xadd(keys.INGEST_STREAM, {"payload": encode(action)})


async def reply(redis: Redis, conn_id: str, message: dict[str, Any]) -> None:
    """Send a message to exactly one connection (via its gateway)."""
    if conn_id:
        await redis.publish(keys.reply(conn_id), encode(message))


async def broadcast(redis: Redis, code: str, message: dict[str, Any]) -> None:
    """Send a message to every gateway subscribed to this room."""
    await redis.publish(keys.room_events(code), encode(message))


async def load_room(redis: Redis, code: str) -> RoomState | None:
    raw = await redis.get(keys.room_state(code))
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        return RoomState.model_validate_json(raw)
    except Exception:  # noqa: BLE001 - a corrupted document must not kill the engine
        return None


async def save_room(
    redis: Redis,
    state: RoomState,
    *,
    new_tokens: list[str] | None = None,
) -> None:
    await redis.set(keys.room_state(state.code), state.model_dump_json())
    await redis.sadd(keys.ROOMS_SET, state.code)
    for token in new_tokens or []:
        await redis.set(keys.token_index(token), state.code)


async def index_token(redis: Redis, token: str, code: str) -> None:
    await redis.set(keys.token_index(token), code)


async def room_code_for_token(redis: Redis, token: str) -> str | None:
    raw = await redis.get(keys.token_index(token))
    if not raw:
        return None
    return raw.decode("utf-8") if isinstance(raw, bytes) else raw


async def delete_room(redis: Redis, state: RoomState) -> None:
    await redis.delete(keys.room_state(state.code))
    await redis.delete(keys.room_votes(state.code))
    await redis.delete(keys.room_lock(state.code))
    for player in state.players:
        await redis.delete(keys.token_index(player.token))
    await redis.srem(keys.ROOMS_SET, state.code)


async def active_room_codes(redis: Redis) -> list[str]:
    raw = await redis.smembers(keys.ROOMS_SET)
    codes = []
    for item in raw:
        codes.append(item.decode("utf-8") if isinstance(item, bytes) else item)
    return codes
