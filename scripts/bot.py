#!/usr/bin/env python3
"""ConsensusKill bot swarm: a CLI smoke-test / load client.

It plays a whole match over real WebSockets, exactly like a browser would:
create a room, join, start, chat, vote, reconnect when a gateway dies.

Examples::

    python scripts/bot.py --url ws://localhost:8000/ws --players 5
    python scripts/bot.py --url ws://localhost:8080/ws --players 8 --strategy random
    python scripts/bot.py --rooms 3 --players 5   # three rooms at once

Exit code 0 means a match reached GAME_OVER.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time

try:  # websockets >= 10
    from websockets.asyncio.client import connect
except ImportError:  # pragma: no cover - fallback for older websockets
    from websockets import connect  # type: ignore[no-redef]


def log(name: str, text: str) -> None:
    stamp = time.strftime("%H:%M:%S")
    print(f"{stamp} [{name}] {text}", flush=True)


class Bot:
    def __init__(self, url: str, name: str, strategy: str, index: int) -> None:
        self.url = url
        self.name = name
        self.strategy = strategy
        self.index = index
        self.ws = None
        self.reader: asyncio.Task | None = None
        self.token: str | None = None
        self.player_id: str | None = None
        self.room_code: str | None = None
        self.is_host = False
        self.instance: str | None = None
        self.joined_event = asyncio.Event()
        self.done_event = asyncio.Event()
        self.stopping = False
        self.last_phase: str | None = None
        self.state: dict | None = None
        self.final_winners: list[str] = []
        self.errors = 0
        self.supervisor: asyncio.Task | None = None
        self._voted_round: int | None = None
        self._last_ignored: tuple | None = None

    # ------------------------------------------------------------- session
    async def run(self, first_action: dict) -> None:
        """Connect, complete ``first_action``, then supervise the session.

        Returns as soon as the room acknowledges the first action (so a
        caller can sequence create -> join -> start); a background task
        keeps the socket alive and reconnects with the token when it dies.
        """
        action = first_action
        attempt = 0
        while not self.stopping:
            try:
                await self._open_session(action)
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - bots must survive gateway kills
                attempt += 1
                delay = min(0.5 * (2 ** min(attempt, 4)), 8.0)
                log(self.name, f"connection lost ({exc}); retrying in {delay:.1f}s")
                await asyncio.sleep(delay)
                action = self._resume_action(first_action)
        if not self.stopping:
            self.supervisor = asyncio.create_task(self._supervise(first_action))

    def _resume_action(self, first_action: dict) -> dict:
        return {"type": "reconnect", "token": self.token} if self.token else first_action

    async def _open_session(self, action: dict) -> None:
        """Connect, send ``action``, and wait until the room acknowledges."""
        self.ws = await connect(self.url, max_size=None, open_timeout=10)
        self.joined_event = asyncio.Event()
        self.reader = asyncio.create_task(self._pump())
        try:
            await self._send(action)
            await asyncio.wait_for(self.joined_event.wait(), 10)
        except BaseException:
            if self.reader and not self.reader.done():
                self.reader.cancel()
            try:
                await self.ws.close()
            except Exception:  # noqa: BLE001
                pass
            raise

    async def _supervise(self, first_action: dict) -> None:
        """Reconnect with the player token whenever the socket dies."""
        action = first_action
        while not self.stopping:
            reader = self.reader
            if reader is None:
                return
            try:
                await reader
            except asyncio.CancelledError:
                if self.stopping:
                    return
                raise
            except Exception:  # noqa: BLE001 - the pump died with the socket
                pass
            if self.stopping:
                return
            action = self._resume_action(first_action)
            if action["type"] != "reconnect":
                return  # no token: nothing to resume
            attempt = 0
            while not self.stopping:
                try:
                    await self._open_session(action)
                    log(self.name, "reconnected")
                    break
                except asyncio.CancelledError:
                    return
                except Exception as exc:  # noqa: BLE001
                    attempt += 1
                    delay = min(0.5 * (2 ** min(attempt, 4)), 8.0)
                    log(self.name, f"reconnect failed ({exc}); retrying in {delay:.1f}s")
                    await asyncio.sleep(delay)
                    action = self._resume_action(first_action)

    async def close(self) -> None:
        self.stopping = True
        for task in (self.supervisor, self.reader):
            if task and not task.done():
                task.cancel()
        for task in (self.supervisor, self.reader):
            if task:
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        if self.ws:
            try:
                await self.ws.close()
            except Exception:  # noqa: BLE001
                pass

    async def _pump(self) -> None:
        assert self.ws is not None
        async for raw in self.ws:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            await self._handle(message)

    async def _send(self, payload: dict) -> None:
        assert self.ws is not None
        await self.ws.send(json.dumps(payload))

    # ------------------------------------------------------------- messages
    async def _handle(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "instance_info":
            self.instance = message.get("instance_id")
            log(self.name, f"connected to instance: {self.instance}")
        elif kind == "joined":
            self.player_id = message.get("player_id")
            self.token = message.get("token")
            self.room_code = message.get("room_code")
            self.is_host = bool(message.get("is_host"))
            self.joined_event.set()
        elif kind == "state":
            await self._on_state(message)
        elif kind == "chat":
            log(f"{self.name}/chat", f"{message.get('nickname')}: {message.get('text')}")
        elif kind == "error":
            self.errors += 1
            log(self.name, f"error {message.get('code')}: {message.get('message')}")
            if message.get("code") in {"kicked", "invalid_token", "room_not_found"}:
                self.stopping = True
                self.done_event.set()
        elif kind == "ack":
            if not message.get("ok"):
                detail = (message.get("request"), message.get("detail"))
                if detail != self._last_ignored:
                    self._last_ignored = detail
                    log(self.name, f"ignored {message.get('request')} ({message.get('detail')})")

    async def _on_state(self, state: dict) -> None:
        self.state = state
        phase = state.get("phase")
        you = state.get("you") or {}
        if phase != self.last_phase:
            self.last_phase = phase
            question = (state.get("question") or {}).get("text", "-")
            log(
                self.name,
                f"room {state.get('room_code')} round {state.get('round_no')} -> {phase}"
                + (f" | {question}" if phase == "QUESTION" else ""),
            )
        if (
            phase == "VOTE"
            and you.get("alive")
            and not you.get("has_voted")
            and self._voted_round != state.get("round_no")
        ):
            choice = self._choose(state)
            self._voted_round = state.get("round_no")
            await self._send({"type": "vote", "choice": choice})
            log(self.name, f"voted {choice}")
        elif phase == "REVEAL" and state.get("counts"):
            counts = state["counts"]
            result = state.get("result") or {}
            log(
                self.name,
                f"reveal A={counts.get('A')} B={counts.get('B')} "
                f"status={result.get('status')} losing={result.get('losing_side')} "
                f"lives={you.get('lives')}",
            )
        elif phase == "GAME_OVER":
            self.final_winners = state.get("winners") or []
            self.stopping = True
            self.done_event.set()

    def _choose(self, state: dict) -> str:
        if self.strategy == "random":
            return random.choice(["A", "B"])
        if self.strategy == "majority":
            return "A"
        # "trap": everyone piles onto A except bot #0, which sits on the
        # minority side B and therefore survives.
        return "B" if self.index == 0 else "A"

    async def say(self, text: str) -> None:
        await self._send({"type": "chat", "text": text})


async def run_room(
    url: str, room_index: int, players: int, strategy: str, max_seconds: float
) -> bool:
    bots = [Bot(url, f"r{room_index}-b{i}", strategy, i) for i in range(players)]
    host = bots[0]
    await host.run({"type": "create_room", "nickname": f"host{room_index}"})
    code = host.room_code
    if not code:
        log(f"r{room_index}", "host failed to create a room")
        return False
    log(f"r{room_index}", f"room {code} created with {players} players")

    async def joiner(bot: Bot) -> None:
        await bot.run({"type": "join", "code": code, "nickname": bot.name})

    await asyncio.gather(*(joiner(bot) for bot in bots[1:]))
    await asyncio.sleep(0.2)
    await host.say("glhf - no mercy")
    await host._send({"type": "host_start"})
    log(f"r{room_index}", "game started")

    try:
        await asyncio.wait_for(
            asyncio.gather(*(bot.done_event.wait() for bot in bots)), timeout=max_seconds
        )
    except TimeoutError:
        log(f"r{room_index}", f"timed out after {max_seconds:.0f}s")
        for bot in bots:
            await bot.close()
        return False

    winners = host.final_winners
    names = [
        bot.name for bot in bots if bot.player_id in winners
    ]
    log(
        f"r{room_index}",
        f"GAME OVER - winners: {', '.join(names) if names else 'nobody (draw)'} "
        f"({len(winners)} survivor ids)",
    )
    for bot in bots:
        await bot.close()
    return bool(host.state) and host.last_phase == "GAME_OVER"


async def main() -> int:
    parser = argparse.ArgumentParser(description="ConsensusKill bot swarm")
    parser.add_argument("--url", default="ws://localhost:8000/ws", help="gateway websocket url")
    parser.add_argument("--players", type=int, default=5, help="players per room (min 3)")
    parser.add_argument("--rooms", type=int, default=1, help="how many parallel rooms")
    parser.add_argument(
        "--strategy", choices=["trap", "random", "majority"], default="trap", help="vote strategy"
    )
    parser.add_argument("--timeout", type=float, default=120.0, help="seconds before giving up")
    args = parser.parse_args()

    if args.players < 3:
        print("--players must be >= 3", file=sys.stderr)
        return 2

    results = await asyncio.gather(
        *(
            run_room(args.url, index, args.players, args.strategy, args.timeout)
            for index in range(args.rooms)
        )
    )
    ok = all(results)
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
