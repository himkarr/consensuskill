"""In-memory socket registry.

This is the *only* state a gateway keeps: which WebSocket belongs to which
connection id, room and player. Everything else lives in Redis, so any client
can land on any gateway and be resumed with its player token.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from starlette.websockets import WebSocket


@dataclass
class Connection:
    conn_id: str
    ws: WebSocket
    player_id: str | None = None
    room_code: str | None = None
    is_host: bool = False
    closed: bool = False
    metadata: dict = field(default_factory=dict)

    @property
    def joined(self) -> bool:
        return self.player_id is not None and self.room_code is not None


class ConnectionRegistry:
    def __init__(self) -> None:
        self._by_id: dict[str, Connection] = {}
        self._by_room: dict[str, set[str]] = {}

    @property
    def count(self) -> int:
        return len(self._by_id)

    def add(self, ws: WebSocket) -> Connection:
        conn = Connection(conn_id=uuid.uuid4().hex, ws=ws)
        self._by_id[conn.conn_id] = conn
        return conn

    def get(self, conn_id: str) -> Connection | None:
        return self._by_id.get(conn_id)

    def remove(self, conn_id: str) -> Connection | None:
        conn = self._by_id.pop(conn_id, None)
        if conn is not None and conn.room_code:
            room = self._by_room.get(conn.room_code)
            if room is not None:
                room.discard(conn_id)
                if not room:
                    self._by_room.pop(conn.room_code, None)
        return conn

    def attach(
        self,
        conn: Connection,
        *,
        player_id: str | None = None,
        room_code: str | None = None,
        is_host: bool = False,
    ) -> None:
        """Bind a connection to its room after the engine confirmed the join."""
        if player_id:
            conn.player_id = player_id
        conn.is_host = is_host
        if room_code and room_code != conn.room_code:
            if conn.room_code:
                old = self._by_room.get(conn.room_code)
                if old is not None:
                    old.discard(conn.conn_id)
                    if not old:
                        self._by_room.pop(conn.room_code, None)
            conn.room_code = room_code
            self._by_room.setdefault(room_code, set()).add(conn.conn_id)
        elif room_code:
            self._by_room.setdefault(room_code, set()).add(conn.conn_id)

    def detach_room(self, conn: Connection) -> None:
        if not conn.room_code:
            return
        room = self._by_room.get(conn.room_code)
        if room is not None:
            room.discard(conn.conn_id)
            if not room:
                self._by_room.pop(conn.room_code, None)
        conn.room_code = None
        conn.player_id = None
        conn.is_host = False

    def all(self) -> list[Connection]:
        """Every live socket on this gateway (used by the keepalive pinger)."""
        return [conn for conn in self._by_id.values() if not conn.closed]

    def in_room(self, code: str) -> list[Connection]:
        ids = self._by_room.get(code) or set()
        return [conn for conn_id in ids if (conn := self._by_id.get(conn_id)) and not conn.closed]

    @property
    def room_count(self) -> int:
        return len(self._by_room)
