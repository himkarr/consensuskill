"""Redis key/channel names. Every key lives behind one helper so the
gateway, the engine and the tests can never disagree about naming."""

from __future__ import annotations

from shared.constants import KEY_PREFIX

# Streams -------------------------------------------------------------------
INGEST_STREAM = f"{KEY_PREFIX}:ingest"  # every client action lands here
INGEST_GROUP = f"{KEY_PREFIX}:engine"  # consumer group for the engine
INGEST_BLOCK_MS = 2000
INGEST_MAXLEN = 10_000  # approximate XADD trim: bounds memory on hosted Redis

# Keys ----------------------------------------------------------------------
ROOM_STATE = f"{KEY_PREFIX}:room:{{code}}:state"  # JSON document (single writer)
ROOMS_SET = f"{KEY_PREFIX}:rooms"  # active room codes
TOKEN_INDEX = f"{KEY_PREFIX}:token:{{token}}"  # token -> room code
ROOM_VOTES_STREAM = f"{KEY_PREFIX}:room:{{code}}:votes"  # durable vote audit log
ROOM_LOCK = f"{KEY_PREFIX}:room:{{code}}:lock"  # SET NX PX engine lock
ENGINE_LOCK = f"{KEY_PREFIX}:lock:engine"  # only one timer loop per instance

# Pub/Sub channels ----------------------------------------------------------
ROOM_EVENTS = f"{KEY_PREFIX}:room:{{code}}:events"  # state/chat fan-out
REPLY = f"{KEY_PREFIX}:reply:{{conn_id}}"  # per-connection request/reply
# Best-effort "there is work in the ingest stream" signal. The engine sleeps
# on this instead of polling XREADGROUP, so a hosted Redis (Upstash: 500K
# commands/month) stays nearly free while the game reacts instantly.
WAKE = f"{KEY_PREFIX}:wake"

# Patterns every gateway subscribes to once at startup, so no gateway ever has
# to re-subscribe while serving traffic (no races, no missed first message).
REPLY_PREFIX = f"{KEY_PREFIX}:reply:"
ROOM_PREFIX = f"{KEY_PREFIX}:room:"
REPLY_PATTERN = f"{KEY_PREFIX}:reply:*"
ROOM_EVENTS_PATTERN = f"{KEY_PREFIX}:room:*:events"
ROOM_EVENTS_SUFFIX = ":events"


def room_state(code: str) -> str:
    return ROOM_STATE.format(code=code.upper())


def room_events(code: str) -> str:
    return ROOM_EVENTS.format(code=code.upper())


def room_votes(code: str) -> str:
    return ROOM_VOTES_STREAM.format(code=code.upper())


def room_lock(code: str) -> str:
    return ROOM_LOCK.format(code=code.upper())


def token_index(token: str) -> str:
    return TOKEN_INDEX.format(token=token)


def reply(conn_id: str) -> str:
    return REPLY.format(conn_id=conn_id)
