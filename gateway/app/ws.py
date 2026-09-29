"""WebSocket front door for the gateway.

Responsibilities (and nothing else):

* terminate the socket, mint a ``conn_id``,
* validate client messages and forward them to the Redis ingest stream,
* subscribe once to the reply/room Pub/Sub patterns and fan events out to the
  local sockets, attaching the per-connection ``you`` block.

No game rules live here - see ``engine/rules.py``.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError

from gateway.app.models import Connection
from shared import bus, keys
from shared.schemas import ClientMessage, build_you, error_message, instance_info_message

logger = logging.getLogger(__name__)

CLIENT_ADAPTER = TypeAdapter(ClientMessage)


def _decode(raw: Any) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8")
    return str(raw)


async def safe_send(conn: Connection, payload: dict[str, Any]) -> bool:
    """Send a JSON message; drop the connection if the socket is gone."""
    if conn.closed:
        return False
    try:
        await conn.ws.send_json(payload)
        return True
    except Exception:  # noqa: BLE001 - client vanished mid-send
        conn.closed = True
        return False


async def handle_connection(app: Any, ws: WebSocket) -> None:
    await ws.accept()
    registry = app.state.registry
    conn = registry.add(ws)
    logger.info("socket open %s", conn.conn_id)

    await safe_send(
        conn,
        instance_info_message(app.state.instance_id),
    )

    try:
        while True:
            raw = await ws.receive_text()
            try:
                message = CLIENT_ADAPTER.validate_json(raw)
            except (ValidationError, ValueError) as exc:
                await safe_send(
                    conn, error_message("bad_message", f"Malformed message: {exc}", conn.conn_id)
                )
                continue
            await dispatch(app, conn, message)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        logger.exception("socket %s crashed", conn.conn_id)
    finally:
        registry.remove(conn.conn_id)
        if conn.joined:
            await bus.send_action(
                app.state.redis,
                {
                    "kind": "disconnect",
                    "conn_id": conn.conn_id,
                    "code": conn.room_code,
                },
            )
        logger.info("socket closed %s", conn.conn_id)


async def dispatch(app: Any, conn: Connection, message: Any) -> None:
    redis = app.state.redis
    kind = message.type

    if kind == "kill_instance":
        if not conn.is_host:
            await safe_send(
                conn, error_message("not_host", "Only the host can kill this gateway", conn.conn_id)
            )
            return
        logger.critical("host requested kill of gateway %s", app.state.instance_id)
        os._exit(1)  # intentional hard kill for the fault-tolerance demo
        return

    action: dict[str, Any] = {"kind": kind, "conn_id": conn.conn_id, "ts": _now()}

    if kind == "create_room":
        action["nickname"] = message.nickname
    elif kind == "join":
        action["code"] = (message.code or "").strip().upper()
        action["nickname"] = message.nickname
    elif kind == "reconnect":
        action["token"] = message.token
    elif kind == "chat":
        action["code"] = conn.room_code
        action["player_id"] = conn.player_id
        action["text"] = message.text
    elif kind == "vote":
        action["code"] = conn.room_code
        action["player_id"] = conn.player_id
        action["choice"] = message.choice
    elif kind in {"host_start", "sync", "leave"}:
        action["code"] = conn.room_code
        action["player_id"] = conn.player_id
    elif kind == "host_kick":
        action["code"] = conn.room_code
        action["player_id"] = conn.player_id  # caller (verified by the engine)
        action["target_id"] = message.player_id  # who gets removed

    await bus.send_action(redis, action)


async def relay_loop(app: Any) -> None:
    """Single Pub/Sub reader: replies + every room event, routed locally."""
    redis = app.state.redis
    pubsub = redis.pubsub()
    await pubsub.psubscribe(keys.REPLY_PATTERN, keys.ROOM_EVENTS_PATTERN)
    logger.info("relay subscribed to %s and %s", keys.REPLY_PATTERN, keys.ROOM_EVENTS_PATTERN)
    try:
        async for message in pubsub.listen():
            if message.get("type") != "pmessage":
                continue
            channel = _decode(message["channel"])
            try:
                data = json.loads(_decode(message["data"]))
            except (ValueError, TypeError):
                continue
            await route_event(app, channel, data)
    finally:
        try:
            await pubsub.aclose()
        except Exception:  # noqa: BLE001
            pass


async def route_event(app: Any, channel: str, data: dict[str, Any]) -> None:
    registry = app.state.registry

    if channel.startswith(keys.REPLY_PREFIX):
        conn = registry.get(channel[len(keys.REPLY_PREFIX) :])
        if conn is None:
            return  # socket already gone; the engine keeps no state for it
        if data.get("type") == "joined":
            registry.attach(
                conn,
                player_id=data.get("player_id"),
                room_code=data.get("room_code"),
                is_host=bool(data.get("is_host")),
            )
        if data.get("type") == "state":
            data = dict(data)
            data["you"] = build_you(data, conn.player_id)
        await safe_send(conn, data)
        return

    if channel.startswith(keys.ROOM_PREFIX) and channel.endswith(keys.ROOM_EVENTS_SUFFIX):
        code = channel[len(keys.ROOM_PREFIX) : -len(keys.ROOM_EVENTS_SUFFIX)]
        for conn in registry.in_room(code):
            payload = dict(data)
            if payload.get("type") == "state":
                payload["you"] = build_you(payload, conn.player_id)
            await safe_send(conn, payload)


def _now() -> float:
    return time.time()
