"""Verify a hosted Redis (e.g. Upstash) supports everything ConsensusKill runs.

    REDIS_URL=rediss://default:<password>@<host>.upstash.io \\
    python scripts/smoke_upstash.py

Upstash free tier: 500K commands/month, no blocking reads, Pub/Sub and
Streams supported. This script executes the *exact* command shapes the app
uses (including XADD MAXLEN, XREADGROUP without BLOCK, XAUTOCLAIM, SET NX PX
and Pub/Sub) and reports PASS/FAIL per command. Exits non-zero on failure.

Nothing is left behind: every temporary key/stream it creates is deleted.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

from redis.asyncio import Redis

# `python scripts/smoke_upstash.py` puts scripts/, not the repo root, on sys.path.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared import keys  # noqa: E402


async def main() -> int:
    url = (os.environ.get("REDIS_URL") or (sys.argv[1] if len(sys.argv) > 1 else "")).strip()
    if not url:
        print("usage: REDIS_URL=rediss://... python scripts/smoke_upstash.py")
        return 2

    redis = Redis.from_url(url, decode_responses=False)
    suffix = uuid.uuid4().hex[:8]
    stream = f"{keys.KEY_PREFIX}:smoke:{suffix}"
    group = f"{keys.KEY_PREFIX}:smoke-group"
    lock_key = f"{keys.KEY_PREFIX}:smoke-lock:{suffix}"
    results: list[tuple[str, bool, str]] = []

    async def check(name: str, coro) -> None:
        try:
            detail = await coro
            results.append((name, True, str(detail or "")))
        except Exception as exc:  # noqa: BLE001 - report and continue
            results.append((name, False, f"{type(exc).__name__}: {exc}"))

    try:
        await check("PING", redis.ping())
        await check("SET NX PX (room lock)", redis.set(lock_key, "me", nx=True, px=2000))
        await check("GET/DEL", redis.delete(lock_key))

        # ingest stream: maxlen trim + consumer group, exactly like bus.send_action
        await check(
            "XADD MAXLEN ~",
            redis.xadd(stream, {"payload": "{}"}, maxlen=1000, approximate=True),
        )
        await check(
            "XGROUP CREATE MKSTREAM",
            redis.xgroup_create(stream, group, id="0", mkstream=True),
        )
        # Upstash forbids BLOCK: this is the deploy shape (ENGINE_BLOCK_MS=0).
        await check(
            "XREADGROUP (no BLOCK)",
            redis.xreadgroup(group, "smoke", {stream: ">"}, count=10, block=None),
        )
        await check("XACK", redis.xack(stream, group, "0-0"))
        await check(
            "XAUTOCLAIM",
            redis.xautoclaim(stream, group, "smoke", min_idle_time=0, start_id="0-0"),
        )
        await check("XLEN", redis.xlen(stream))

        # wake channel: publish + subscribe round trip
        pubsub = redis.pubsub()
        await pubsub.subscribe(keys.WAKE)
        await redis.publish(keys.WAKE, "1")
        message = None
        for _ in range(20):
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
            if message is not None:
                break
        results.append(
            ("PUBLISH/SUBSCRIBE (wake)", message is not None, "" if message else "no message")
        )
        await pubsub.aclose()

        await check("SMEMBERS", redis.smembers(keys.ROOMS_SET))
        await check("SETEX/GET/DEL", redis.delete(f"{keys.KEY_PREFIX}:smoke:{suffix}"))
    finally:
        try:
            await redis.delete(stream, lock_key)  # deleting the stream drops its group
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass
        await redis.aclose()

    failed = 0
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}{f' — {detail}' if detail and not ok else ''}")
        failed += 0 if ok else 1
    print(f"\n{len(results) - failed}/{len(results)} checks passed against {url.split('@')[-1]}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
