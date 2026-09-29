"""Pydantic models shared by the gateway, the round engine and the tests."""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class Phase(str, Enum):
    LOBBY = "LOBBY"
    QUESTION = "QUESTION"
    DISCUSSION = "DISCUSSION"
    VOTE = "VOTE"
    REVEAL = "REVEAL"
    APPLY = "APPLY"
    GAME_OVER = "GAME_OVER"


class Choice(str, Enum):
    A = "A"
    B = "B"


class Player(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    nickname: str
    token: str = Field(default_factory=lambda: uuid.uuid4().hex + uuid.uuid4().hex)
    lives: int = 2
    alive: bool = True
    connected: bool = False
    is_host: bool = False
    joined_at: float = Field(default_factory=time.time)


class Question(BaseModel):
    id: str
    text: str
    option_a: str
    option_b: str


class ChatMessage(BaseModel):
    player_id: str
    nickname: str
    text: str
    ts: float = Field(default_factory=time.time)


class RoomState(BaseModel):
    """The single authoritative document for a room.

    It is stored as one JSON string in Redis and rewritten with a plain ``SET``
    so a phase transition is always atomic.
    """

    code: str
    phase: Phase = Phase.LOBBY
    players: list[Player] = Field(default_factory=list)
    host_id: str = ""
    # conn_id -> player_id for every live socket attached to this room. This is
    # how the engine knows who is online; gateways only keep their own sockets.
    conns: dict[str, str] = Field(default_factory=dict)
    round_no: int = 0
    question: Question | None = None
    used_question_ids: list[str] = Field(default_factory=list)
    # player_id -> "A" | "B" | None (None == abstain). Pre-filled for every
    # eligible voter when the VOTE phase opens; only the first vote counts.
    votes: dict[str, str | None] = Field(default_factory=dict)
    counts: dict[str, int] | None = None
    result: dict[str, Any] | None = None
    timer_ends_at: float | None = None
    winners: list[str] = Field(default_factory=list)
    chat_log: list[ChatMessage] = Field(default_factory=list)
    chat_last_at: dict[str, float] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    version: int = 0

    def player_by_id(self, player_id: str) -> Player | None:
        for player in self.players:
            if player.id == player_id:
                return player
        return None

    def player_by_token(self, token: str) -> Player | None:
        for player in self.players:
            if player.token == token:
                return player
        return None

    def player_by_nickname(self, nickname: str) -> Player | None:
        lowered = nickname.casefold()
        for player in self.players:
            if player.nickname.casefold() == lowered:
                return player
        return None

    @property
    def alive_players(self) -> list[Player]:
        return [p for p in self.players if p.alive]


# ---------------------------------------------------------------------------
# Client -> server messages
# ---------------------------------------------------------------------------
class CreateRoomMessage(BaseModel):
    type: Literal["create_room"]
    nickname: str


class JoinMessage(BaseModel):
    type: Literal["join"]
    code: str
    nickname: str


class ReconnectMessage(BaseModel):
    type: Literal["reconnect"]
    token: str


class ChatOutMessage(BaseModel):
    type: Literal["chat"]
    text: str


class VoteMessage(BaseModel):
    type: Literal["vote"]
    choice: str


class HostStartMessage(BaseModel):
    type: Literal["host_start"]


class HostKickMessage(BaseModel):
    type: Literal["host_kick"]
    player_id: str


class KillInstanceMessage(BaseModel):
    type: Literal["kill_instance"]


class SyncMessage(BaseModel):
    type: Literal["sync"]


class LeaveMessage(BaseModel):
    type: Literal["leave"]


ClientMessage = Annotated[
    CreateRoomMessage
    | JoinMessage
    | ReconnectMessage
    | ChatOutMessage
    | VoteMessage
    | HostStartMessage
    | HostKickMessage
    | KillInstanceMessage
    | SyncMessage
    | LeaveMessage,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Server -> client message builders (the wire protocol lives here so gateway
# and engine can never drift apart).
# ---------------------------------------------------------------------------
def instance_info_message(instance_id: str, server_time: float | None = None) -> dict[str, Any]:
    return {
        "type": "instance_info",
        "instance_id": instance_id,
        "server_time": server_time if server_time is not None else time.time(),
    }


def joined_message(
    conn_id: str,
    player_id: str,
    token: str,
    room_code: str,
    is_host: bool,
    server_time: float | None = None,
) -> dict[str, Any]:
    return {
        "type": "joined",
        "conn_id": conn_id,
        "player_id": player_id,
        "token": token,
        "room_code": room_code,
        "is_host": is_host,
        "server_time": server_time if server_time is not None else time.time(),
    }


def state_message(payload: dict[str, Any], server_time: float | None = None) -> dict[str, Any]:
    message = dict(payload)
    message["type"] = "state"
    message["server_time"] = server_time if server_time is not None else time.time()
    return message


def chat_message(chat: dict[str, Any]) -> dict[str, Any]:
    message = dict(chat)
    message["type"] = "chat"
    return message


def error_message(code: str, message: str, conn_id: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"type": "error", "code": code, "message": message}
    if conn_id is not None:
        payload["conn_id"] = conn_id
    return payload


def ack_message(
    request: str, ok: bool, detail: str = "", conn_id: str | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {"type": "ack", "request": request, "ok": ok, "detail": detail}
    if conn_id is not None:
        payload["conn_id"] = conn_id
    return payload


def build_you(payload: dict[str, Any], player_id: str | None) -> dict[str, Any] | None:
    """Attach the per-connection ``you`` block to a broadcast state message.

    Gateways call this for every local socket; the engine also uses it when it
    answers a direct ``sync`` request. The block only ever exposes the caller's
    own vote, and only once the round has been revealed.
    """
    if not player_id:
        return None
    votes = payload.get("votes") or {}
    for player in payload.get("players", []):
        if player.get("id") == player_id:
            return {
                "player_id": player_id,
                "nickname": player.get("nickname"),
                "lives": player.get("lives"),
                "alive": player.get("alive"),
                "is_host": player.get("is_host"),
                "has_voted": player.get("has_voted"),
                "choice": votes.get(player_id),
            }
    return None
