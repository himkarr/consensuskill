"""Redis helpers that only the round engine needs: the per-room lock and the
durable vote audit stream."""

from __future__ import annotations

import asyncio
import time

from redis.asyncio import Redis

from shared import bus, keys

DEFAULT_LOCK_TTL_MS = 5_000
DEFAULT_WAIT_S = 2.0
DEFAULT_POLL_S = 0.02


async def acquire_lock(
    redis: Redis,
    code: str,
    owner: str,
    *,
    ttl_ms: int = DEFAULT_LOCK_TTL_MS,
    wait_s: float = DEFAULT_WAIT_S,
) -> bool:
    """SET NX PX lock with a bounded wait.

    Only the engine holding the lock may mutate a room document, so a second
    engine replica (or a restarted one) can never double-advance a round.
    """
    key = keys.room_lock(code)
    deadline = time.monotonic() + wait_s
    while True:
        if await redis.set(key, owner, nx=True, px=ttl_ms):
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(DEFAULT_POLL_S)


async def release_lock(redis: Redis, code: str, owner: str) -> None:
    """Release only if we still own the lock (compare-then-delete)."""
    key = keys.room_lock(code)
    current = await redis.get(key)
    if current is None:
        return
    if isinstance(current, bytes):
        current = current.decode("utf-8")
    if current == owner:
        await redis.delete(key)


async def audit_vote(
    redis: Redis,
    code: str,
    *,
    player_id: str,
    choice: str,
    round_no: int,
) -> None:
    """Append the vote to the per-room stream (durability + the Streams demo)."""
    await redis.xadd(
        keys.room_votes(code),
        {
            "payload": bus.encode(
                {
                    "player_id": player_id,
                    "choice": choice,
                    "round_no": round_no,
                    "ts": time.time(),
                }
            )
        },
        maxlen=1000,
    )


class RoomLock:
    """``async with RoomLock(redis, code, owner): ...``"""

    def __init__(self, redis: Redis, code: str, owner: str) -> None:
        self.redis = redis
        self.code = code
        self.owner = owner
        self.acquired = False

    async def __aenter__(self) -> bool:
        self.acquired = await acquire_lock(self.redis, self.code, self.owner)
        return self.acquired

    async def __aexit__(self, *exc_info: object) -> None:
        if self.acquired:
            await release_lock(self.redis, self.code, self.owner)
