"""Game constants shared by gateway, engine and tests."""

from __future__ import annotations

from shared.schemas import Phase

APP_NAME = "consensuskill"

# --- room / player ---------------------------------------------------------
ROOM_CODE_LENGTH = 6
# No I, L, O, 0, 1 to avoid transcription mistakes when reading a projector screen.
ROOM_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
MIN_PLAYERS_TO_START = 3
MAX_PLAYERS = 64
STARTING_LIVES = 2
NICKNAME_MAX_LENGTH = 16
CHAT_MAX_LENGTH = 200
CHAT_LOG_LIMIT = 50

# --- phases / timers (seconds) --------------------------------------------
QUESTION_SECONDS = 5
DISCUSSION_SECONDS = 20
VOTE_SECONDS = 10
REVEAL_SECONDS = 8
APPLY_SECONDS = 5

PHASE_DURATIONS: dict[Phase, int | None] = {
    Phase.LOBBY: None,
    Phase.QUESTION: QUESTION_SECONDS,
    Phase.DISCUSSION: DISCUSSION_SECONDS,
    Phase.VOTE: VOTE_SECONDS,
    Phase.REVEAL: REVEAL_SECONDS,
    Phase.APPLY: APPLY_SECONDS,
    Phase.GAME_OVER: None,
}

# Phases in which vote tallies may be shown to clients.
REVEAL_PHASES: frozenset[Phase] = frozenset({Phase.REVEAL, Phase.APPLY, Phase.GAME_OVER})

# --- chat ------------------------------------------------------------------
CHAT_RATE_LIMIT_PER_SECOND = 1.0

# --- redis -----------------------------------------------------------------
KEY_PREFIX = "ck"
