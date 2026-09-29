"""Pure game rules.

Every function in this module is side-effect free with respect to the outside
world: no Redis, no sockets, no timers. State is a :class:`RoomState` that the
caller owns. That makes the whole rule set unit-testable without any service.
"""

from __future__ import annotations

import re
import secrets
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, NamedTuple

from shared.constants import (
    CHAT_LOG_LIMIT,
    CHAT_MAX_LENGTH,
    CHAT_RATE_LIMIT_PER_SECOND,
    MAX_PLAYERS,
    MIN_PLAYERS_TO_START,
    NICKNAME_MAX_LENGTH,
    PHASE_DURATIONS,
    REVEAL_PHASES,
    ROOM_CODE_ALPHABET,
    ROOM_CODE_LENGTH,
    STARTING_LIVES,
)
from shared.errors import GameError
from shared.schemas import (
    ChatMessage,
    Phase,
    Player,
    Question,
    RoomState,
    build_you,
    state_message,
)

CHAT_ENABLED_PHASES = frozenset(
    {
        Phase.LOBBY,
        Phase.QUESTION,
        Phase.DISCUSSION,
        Phase.REVEAL,
        Phase.APPLY,
        Phase.GAME_OVER,
    }
)

_WHITESPACE_RE = re.compile(r"\s+")


class VoteOutcome(NamedTuple):
    accepted: bool
    reason: str  # ok | duplicate | wrong_phase | not_eligible | invalid_choice | too_late


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def generate_room_code(
    length: int = ROOM_CODE_LENGTH,
    is_taken: Callable[[str], bool] | None = None,
    max_attempts: int = 50,
) -> str:
    """Random 6-char room code; retries while ``is_taken`` says it exists."""
    for _ in range(max_attempts):
        code = "".join(secrets.choice(ROOM_CODE_ALPHABET) for _ in range(length))
        if is_taken is None or not is_taken(code):
            return code
    raise GameError("code_exhausted", "Could not allocate a unique room code")


def sanitize_text(text: str, max_length: int = CHAT_MAX_LENGTH) -> str:
    """Strip control characters, collapse whitespace, truncate."""
    if not isinstance(text, str):
        return ""
    cleaned = "".join(ch for ch in text if ch.isprintable() or ch in " \t")
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned[:max_length]


def validate_nickname(nickname: str) -> str:
    cleaned = sanitize_text(nickname, NICKNAME_MAX_LENGTH)
    if not cleaned:
        raise GameError("invalid_nickname", "Nickname must not be empty")
    return cleaned


def set_phase(state: RoomState, phase: Phase, now: float) -> None:
    duration = PHASE_DURATIONS[phase]
    state.phase = phase
    state.timer_ends_at = now + duration if duration is not None else None
    state.version += 1
    state.updated_at = now


# ---------------------------------------------------------------------------
# lobby
# ---------------------------------------------------------------------------
def create_room(
    host_nickname: str,
    *,
    now: float | None = None,
    code: str | None = None,
    is_taken: Callable[[str], bool] | None = None,
) -> RoomState:
    """Create a fresh room in LOBBY with ``host_nickname`` as the host."""
    now = time.time() if now is None else now
    nickname = validate_nickname(host_nickname)
    room_code = code.upper() if code else generate_room_code(is_taken=is_taken)
    if is_taken is not None and is_taken(room_code):
        raise GameError("room_exists", "That room code is already in use")

    host = Player(nickname=nickname, is_host=True, connected=True, joined_at=now)
    state = RoomState(
        code=room_code,
        phase=Phase.LOBBY,
        players=[host],
        host_id=host.id,
        created_at=now,
        updated_at=now,
    )
    return state


def join_room(state: RoomState, nickname: str, *, now: float | None = None) -> Player:
    """Add a player to a LOBBY room and return the created player."""
    now = time.time() if now is None else now
    if state.phase is not Phase.LOBBY:
        raise GameError("game_in_progress", "This game has already started")
    if len(state.players) >= MAX_PLAYERS:
        raise GameError("room_full", f"Room is full (max {MAX_PLAYERS} players)")

    cleaned = validate_nickname(nickname)
    if state.player_by_nickname(cleaned) is not None:
        raise GameError("nickname_taken", "That nickname is already taken in this room")

    player = Player(nickname=cleaned, connected=True, joined_at=now)
    state.players.append(player)
    state.updated_at = now
    state.version += 1
    return player


def handle_reconnect(state: RoomState, token: str, *, now: float | None = None) -> Player:
    """Restore a player from a reconnect token and mark them connected."""
    now = time.time() if now is None else now
    if not token:
        raise GameError("invalid_token", "Missing player token")
    player = state.player_by_token(token)
    if player is None:
        raise GameError("invalid_token", "Unknown or expired player token")
    player.connected = True
    state.updated_at = now
    state.version += 1
    return player


def handle_disconnect(state: RoomState, player_id: str, *, now: float | None = None) -> None:
    player = state.player_by_id(player_id)
    if player is None:
        return
    player.connected = False
    state.updated_at = time.time() if now is None else now


def host_kick(
    state: RoomState, actor_id: str, target_id: str, *, now: float | None = None
) -> Player:
    """Remove a player from the lobby. Host only, no self-kick."""
    now = time.time() if now is None else now
    if actor_id != state.host_id:
        raise GameError("not_host", "Only the host can kick players")
    if actor_id == target_id:
        raise GameError("invalid_target", "You cannot kick yourself")
    target = state.player_by_id(target_id)
    if target is None:
        raise GameError("not_found", "Player not found")
    state.players.remove(target)
    if state.host_id == target_id:  # pragma: no cover - guarded above
        state.host_id = state.players[0].id if state.players else ""
    state.updated_at = now
    state.version += 1
    return target


# ---------------------------------------------------------------------------
# questions / round lifecycle
# ---------------------------------------------------------------------------
def pick_question(state: RoomState, questions: Sequence[Question]) -> Question:
    if not questions:
        raise GameError("no_questions", "No questions available")
    unused = [q for q in questions if q.id not in state.used_question_ids]
    if not unused:
        state.used_question_ids = []
        unused = list(questions)
    question = unused[0]
    state.used_question_ids.append(question.id)
    return question


def start_game(
    state: RoomState,
    questions: Sequence[Question],
    *,
    now: float | None = None,
) -> RoomState:
    """Start (or restart) a match. Requires >= MIN_PLAYERS_TO_START players."""
    now = time.time() if now is None else now
    if len(state.players) < MIN_PLAYERS_TO_START:
        raise GameError(
            "not_enough_players",
            f"Need at least {MIN_PLAYERS_TO_START} players to start",
        )
    for player in state.players:
        player.lives = STARTING_LIVES
        player.alive = True
    state.winners = []
    state.round_no = 0
    state.votes = {}
    state.counts = None
    state.result = None
    state.question = None
    state.chat_last_at = {}
    _begin_round(state, questions, now)
    return state


def next_question(
    state: RoomState,
    questions: Sequence[Question],
    *,
    now: float | None = None,
) -> RoomState:
    """Move to the next round with a fresh question (used after a void round)."""
    now = time.time() if now is None else now
    if state.phase is Phase.GAME_OVER:
        raise GameError("game_over", "The game is over")
    _begin_round(state, questions, now)
    return state


def _begin_round(state: RoomState, questions: Sequence[Question], now: float) -> None:
    state.question = pick_question(state, questions)
    state.round_no += 1
    state.votes = {}
    state.counts = None
    state.result = None
    state.winners = []
    set_phase(state, Phase.QUESTION, now)


# ---------------------------------------------------------------------------
# voting
# ---------------------------------------------------------------------------
def submit_vote(
    state: RoomState,
    player_id: str,
    choice: str | None,
    *,
    now: float | None = None,
) -> VoteOutcome:
    """Record a vote. Only the first valid vote per player per round counts."""
    now = time.time() if now is None else now

    if state.phase is not Phase.VOTE:
        return VoteOutcome(False, "wrong_phase")
    if state.timer_ends_at is not None and now > state.timer_ends_at:
        return VoteOutcome(False, "too_late")
    if player_id not in state.votes:
        # Spectators, eliminated players and players outside this round.
        return VoteOutcome(False, "not_eligible")
    if state.votes[player_id] is not None:
        return VoteOutcome(False, "duplicate")
    if choice not in ("A", "B"):
        return VoteOutcome(False, "invalid_choice")

    state.votes[player_id] = choice
    state.updated_at = now
    state.version += 1
    return VoteOutcome(True, "ok")


def compute_minority(votes: Mapping[str, str | None]) -> dict[str, Any]:
    """Resolve a vote split.

    * ``counts`` only counts *cast* votes; ``None``/missing = abstain.
    * ``tie``       -> equal cast votes, nobody loses a life.
    * ``unanimous`` -> every eligible voter cast the same vote (or nobody
      voted at all), so no minority exists and the round is void.
    * ``normal``    -> the majority side loses a life; the minority survives.
      Players who did not vote are treated as majority-side losses, so
      abstaining never beats voting with the majority.
    """
    counts: dict[str, int] = {"A": 0, "B": 0}
    abstainers: list[str] = []
    for player_id, choice in votes.items():
        if choice == "A":
            counts["A"] += 1
        elif choice == "B":
            counts["B"] += 1
        else:
            abstainers.append(player_id)

    total = counts["A"] + counts["B"]
    one_sided = counts["A"] == 0 or counts["B"] == 0
    if total == 0 or (one_sided and not abstainers):
        status = "unanimous"
    elif counts["A"] == counts["B"]:
        status = "tie"
    else:
        status = "normal"

    if status != "normal":
        return {
            "status": status,
            "losing_side": None,
            "majority_side": None,
            "minority_side": None,
            "counts": counts,
            "total_votes": total,
            "abstainers": list(abstainers),
            "losers": [],
            "survivors": list(votes.keys()),
        }

    losing_side = "A" if counts["A"] > counts["B"] else "B"
    majority_side = losing_side
    minority_side = "B" if losing_side == "A" else "A"
    losers: list[str] = []
    survivors: list[str] = []
    for player_id, choice in votes.items():
        if choice is None or choice == losing_side:
            losers.append(player_id)
        else:
            survivors.append(player_id)

    return {
        "status": "normal",
        "losing_side": losing_side,
        "majority_side": majority_side,
        "minority_side": minority_side,
        "counts": counts,
        "total_votes": total,
        "abstainers": list(abstainers),
        "losers": losers,
        "survivors": survivors,
    }


def apply_lives(players: Iterable[Player], result: Mapping[str, Any] | None) -> list[str]:
    """Subtract one life from every loser. Returns the affected player ids."""
    if not result or result.get("status") != "normal":
        return []
    losers = set(result.get("losers") or [])
    affected: list[str] = []
    for player in players:
        if player.id in losers and player.alive and player.lives > 0:
            player.lives -= 1
            affected.append(player.id)
    return affected


def eliminate_players(players: Iterable[Player]) -> list[str]:
    """Turn every player at 0 lives into a spectator. Returns their ids."""
    eliminated: list[str] = []
    for player in players:
        if player.alive and player.lives <= 0:
            player.lives = 0
            player.alive = False
            eliminated.append(player.id)
    return eliminated


def check_winner(
    players: Iterable[Player],
    no_change: bool = False,
) -> list[str]:
    """Return the winning player ids, or ``[]`` if the match must continue.

    The match ends when one player is left, or when at most two remain after a
    round that changed nothing (a 1v1 can never resolve, so it is a draw).
    """
    alive = [p.id for p in players if p.alive]
    if not alive:
        return []
    if len(alive) == 1:
        return alive
    if len(alive) <= 2 and no_change:
        return alive
    return []


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------
def chat_allowed(state: RoomState, player_id: str, now: float | None = None) -> bool:
    """Per-player chat rate limit (default: 1 message/second)."""
    now = time.time() if now is None else now
    last = state.chat_last_at.get(player_id, 0.0)
    return (now - last) >= CHAT_RATE_LIMIT_PER_SECOND


def can_chat(state: RoomState) -> bool:
    return state.phase in CHAT_ENABLED_PHASES


def append_chat(
    state: RoomState, player: Player, text: str, now: float | None = None
) -> ChatMessage:
    now = time.time() if now is None else now
    cleaned = sanitize_text(text)
    if not cleaned:
        raise GameError("empty_message", "Message is empty")
    message = ChatMessage(
        player_id=player.id, nickname=player.nickname, text=cleaned, ts=now
    )
    state.chat_log.append(message)
    if len(state.chat_log) > CHAT_LOG_LIMIT:
        del state.chat_log[: len(state.chat_log) - CHAT_LOG_LIMIT]
    state.chat_last_at[player.id] = now
    state.updated_at = now
    return message


# ---------------------------------------------------------------------------
# phase machine
# ---------------------------------------------------------------------------
def apply_round_result(state: RoomState) -> list[str]:
    """Apply the reveal result to lives and eliminate the dead."""
    affected = apply_lives(state.players, state.result)
    eliminate_players(state.players)
    return affected


def advance_on_timeout(
    state: RoomState,
    questions: Sequence[Question],
    *,
    now: float | None = None,
) -> RoomState:
    """Advance one phase when its timer expired. Pure given ``now``."""
    now = time.time() if now is None else now
    phase = state.phase

    if phase is Phase.QUESTION:
        set_phase(state, Phase.DISCUSSION, now)
    elif phase is Phase.DISCUSSION:
        state.votes = {p.id: None for p in state.alive_players}
        state.counts = None
        state.result = None
        set_phase(state, Phase.VOTE, now)
    elif phase is Phase.VOTE:
        state.result = compute_minority(state.votes)
        state.counts = state.result["counts"]
        set_phase(state, Phase.REVEAL, now)
    elif phase is Phase.REVEAL:
        apply_round_result(state)
        set_phase(state, Phase.APPLY, now)
    elif phase is Phase.APPLY:
        if not state.alive_players:
            # Every player was on the majority side (or abstained) and hit 0.
            state.winners = []
            set_phase(state, Phase.GAME_OVER, now)
            return state
        no_change = not state.result or state.result.get("status") != "normal"
        winners = check_winner(state.players, no_change=no_change)
        if winners:
            state.winners = winners
            set_phase(state, Phase.GAME_OVER, now)
        else:
            next_question(state, questions, now=now)
    return state


# ---------------------------------------------------------------------------
# serialization
# ---------------------------------------------------------------------------
def player_public(player: Player, state: RoomState) -> dict[str, Any]:
    has_voted = player.id in state.votes and state.votes[player.id] is not None
    return {
        "id": player.id,
        "nickname": player.nickname,
        "lives": player.lives,
        "alive": player.alive,
        "connected": player.connected,
        "is_host": player.id == state.host_id,
        "has_voted": has_voted,
    }


def broadcast_state(state: RoomState, for_player_id: str | None = None) -> dict[str, Any]:
    """Build the public, per-connection state message.

    Vote tallies are only included once the round has been revealed and the
    voter's own choice is only ever exposed to that voter (after the reveal).
    """
    revealed = state.phase in REVEAL_PHASES
    result = None
    if revealed and state.result:
        result = {
            "status": state.result.get("status"),
            "losing_side": state.result.get("losing_side"),
            "majority_side": state.result.get("majority_side"),
            "minority_side": state.result.get("minority_side"),
            "abstainers": len(state.result.get("abstainers") or []),
            "losers": state.result.get("losers") or [],
            "survivors": state.result.get("survivors") or [],
        }

    payload: dict[str, Any] = {
        "room_code": state.code,
        "phase": state.phase.value,
        "round_no": state.round_no,
        "timer_ends_at": state.timer_ends_at,
        "server_time": time.time(),
        "question": (
            state.question.model_dump()
            if (state.question and state.phase is not Phase.LOBBY)
            else None
        ),
        "players": [player_public(p, state) for p in state.players],
        "counts": dict(state.counts) if (revealed and state.counts) else None,
        # Everyone's choice is public only after the reveal.
        "votes": dict(state.votes) if (revealed and state.votes) else None,
        "result": result,
        "winners": list(state.winners) if state.phase is Phase.GAME_OVER else [],
        "chat_log": [m.model_dump() for m in state.chat_log],
        "min_players": MIN_PLAYERS_TO_START,
        "starting_lives": STARTING_LIVES,
        "host_id": state.host_id,
        "version": state.version,
    }

    if for_player_id is not None:
        payload["you"] = build_you(payload, for_player_id)

    return state_message(payload)
