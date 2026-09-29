from __future__ import annotations

import pytest

from engine import rules
from shared.questions import load_json_bank
from shared.schemas import Phase, RoomState


@pytest.fixture(scope="session")
def questions() -> list:
    return load_json_bank()


@pytest.fixture
def make_room(questions):
    """Build a started room with ``n`` players (host + joiners)."""

    def _make(n: int = 4, *, start: bool = True) -> RoomState:
        state = rules.create_room("host", code="ABCDEF")
        for index in range(1, n):
            rules.join_room(state, f"player{index}")
        if start:
            rules.start_game(state, questions, now=1_700_000_000.0)
        return state

    return _make


@pytest.fixture
def vote_room(questions, make_room):
    """A room parked in the VOTE phase with ``n`` eligible voters."""

    def _make(n: int = 4) -> RoomState:
        state = make_room(n)
        state.votes = {p.id: None for p in state.alive_players}
        state.phase = Phase.VOTE
        state.timer_ends_at = 1_700_000_035.0
        return state

    return _make
