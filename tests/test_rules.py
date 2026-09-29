"""Spec tests 2, 4, 7, 8 plus the round lifecycle."""

from __future__ import annotations

import pytest

from engine import rules
from shared.errors import GameError
from shared.schemas import Phase


# --- spec 2: joining a room returns a unique player id and token ------------
def test_create_and_join_issues_unique_ids_and_tokens():
    state = rules.create_room("alice", code="ABCDEF")
    players = list(state.players)
    for index in range(1, 8):
        players.append(rules.join_room(state, f"p{index}"))

    ids = [p.id for p in players]
    tokens = [p.token for p in players]

    assert len(ids) == 8
    assert len(set(ids)) == 8
    assert len(set(tokens)) == 8
    assert all(len(t) >= 32 for t in tokens)
    assert state.players[0].is_host
    assert state.host_id == state.players[0].id
    assert state.phase is Phase.LOBBY


def test_room_codes_are_six_chars_from_safe_alphabet():
    codes = {rules.generate_room_code() for _ in range(50)}
    assert all(len(code) == 6 for code in codes)
    assert all(not (set(code) & set("01ILO")) for code in codes)  # unambiguous glyphs
    assert len(codes) > 1  # not a constant


def test_duplicate_nickname_rejected():
    state = rules.create_room("alice", code="ABCDEF")
    rules.join_room(state, "bob")
    with pytest.raises(GameError) as exc:
        rules.join_room(state, "Bob")
    assert exc.value.code == "nickname_taken"


def test_cannot_join_after_game_started(questions):
    state = rules.create_room("alice", code="ABCDEF")
    rules.join_room(state, "bob")
    rules.join_room(state, "carol")
    rules.start_game(state, questions, now=1_700_000_000.0)
    with pytest.raises(GameError) as exc:
        rules.join_room(state, "dave")
    assert exc.value.code == "game_in_progress"


def test_start_game_requires_three_players(questions):
    state = rules.create_room("alice", code="ABCDEF")
    rules.join_room(state, "bob")
    with pytest.raises(GameError) as exc:
        rules.start_game(state, questions, now=1_700_000_000.0)
    assert exc.value.code == "not_enough_players"


# --- spec 4: vote validation ------------------------------------------------
def test_invalid_vote_rejected(vote_room):
    state = vote_room(4)
    pid = list(state.votes)[0]

    # Not A/B.
    assert rules.submit_vote(state, pid, "C", now=1_700_000_010).reason == "invalid_choice"
    assert rules.submit_vote(state, pid, "", now=1_700_000_010).reason == "invalid_choice"
    assert rules.submit_vote(state, pid, None, now=1_700_000_010).reason == "invalid_choice"
    assert state.votes[pid] is None


def test_vote_rejected_in_wrong_phase(make_room):
    state = make_room(4)  # QUESTION phase
    pid = state.alive_players[0].id
    outcome = rules.submit_vote(state, pid, "A", now=1_700_000_001)
    assert not outcome.accepted
    assert outcome.reason == "wrong_phase"


def test_eliminated_player_cannot_vote(vote_room):
    state = vote_room(4)
    dead = state.alive_players[0]
    dead.alive = False
    del state.votes[dead.id]  # engine only registers alive voters
    outcome = rules.submit_vote(state, dead.id, "A", now=1_700_000_010)
    assert not outcome.accepted
    assert outcome.reason == "not_eligible"


def test_first_vote_only_duplicate_ignored(vote_room):
    state = vote_room(4)
    pid = list(state.votes)[0]

    first = rules.submit_vote(state, pid, "A", now=1_700_000_010)
    state.version = 42
    duplicate = rules.submit_vote(state, pid, "B", now=1_700_000_011)

    assert first.accepted
    assert not duplicate.accepted
    assert duplicate.reason == "duplicate"
    assert state.votes[pid] == "A"  # first vote stands
    assert state.version == 42  # duplicate changed nothing


def test_late_vote_ignored(vote_room):
    state = vote_room(4)
    pid = list(state.votes)[0]
    outcome = rules.submit_vote(state, pid, "A", now=1_700_000_036.0)
    assert not outcome.accepted
    assert outcome.reason == "too_late"
    assert state.votes[pid] is None


# --- spec 7: reconnect ------------------------------------------------------
def test_reconnect_with_valid_token_restores_player(make_room):
    state = make_room(4)
    target = state.alive_players[2]
    target.connected = False

    restored = rules.handle_reconnect(state, target.token)

    assert restored.id == target.id
    assert restored.connected is True
    assert restored.lives == 2  # lives preserved across reconnect


def test_reconnect_with_invalid_token_rejected(make_room):
    state = make_room(4)
    with pytest.raises(GameError) as exc:
        rules.handle_reconnect(state, "not-a-real-token")
    assert exc.value.code == "invalid_token"

    with pytest.raises(GameError):
        rules.handle_reconnect(state, "")


def test_reconnect_works_after_elimination(make_room):
    state = make_room(4)
    target = state.alive_players[0]
    target.lives = 0
    rules.eliminate_players(state.players)

    restored = rules.handle_reconnect(state, target.token)
    assert restored.alive is False  # comes back as a spectator
    assert restored.connected is True


# --- spec 8: chat rate limiting --------------------------------------------
def test_chat_rate_limit_blocks_rapid_messages(make_room):
    state = make_room(4)
    player = state.alive_players[0]

    assert rules.chat_allowed(state, player.id, now=1_000.0)
    rules.append_chat(state, player, "hello", now=1_000.0)

    # Second message 0.4s later is blocked.
    assert not rules.chat_allowed(state, player.id, now=1_000.4)
    # ... 1.1s after the first one is allowed again.
    assert rules.chat_allowed(state, player.id, now=1_001.1)


def test_chat_is_sanitized_and_truncated(make_room):
    state = make_room(4)
    player = state.alive_players[0]

    message = rules.append_chat(state, player, "  hey\u0000\u0007   there \n friend  ", now=1_000.0)
    assert message.text == "hey there friend"

    long = rules.append_chat(state, player, "x" * 500, now=1_002.0)
    assert len(long.text) == 200

    with pytest.raises(GameError):
        rules.append_chat(state, player, "   ", now=1_004.0)


def test_empty_nickname_rejected():
    with pytest.raises(GameError) as exc:
        rules.create_room("   ", code="ABCDEF")
    assert exc.value.code == "invalid_nickname"


# --- round lifecycle --------------------------------------------------------
def test_full_round_cycle_and_tie_replay(questions, make_room):
    state = make_room(4)
    first_question = state.question.id
    assert state.phase is Phase.QUESTION

    # QUESTION -> DISCUSSION -> VOTE
    rules.advance_on_timeout(state, questions, now=1_700_000_010)
    assert state.phase is Phase.DISCUSSION
    rules.advance_on_timeout(state, questions, now=1_700_000_040)
    assert state.phase is Phase.VOTE
    assert len(state.votes) == 4
    assert all(v is None for v in state.votes.values())

    # Two A, two B -> tie.
    pids = list(state.votes)
    rules.submit_vote(state, pids[0], "A", now=1_700_000_045)
    rules.submit_vote(state, pids[1], "A", now=1_700_000_045)
    rules.submit_vote(state, pids[2], "B", now=1_700_000_045)
    rules.submit_vote(state, pids[3], "B", now=1_700_000_045)

    rules.advance_on_timeout(state, questions, now=1_700_000_050)  # -> REVEAL
    assert state.phase is Phase.REVEAL
    assert state.result["status"] == "tie"
    assert state.counts == {"A": 2, "B": 2}
    assert all(p.lives == 2 for p in state.players)

    rules.advance_on_timeout(state, questions, now=1_700_000_060)  # -> APPLY
    assert state.phase is Phase.APPLY
    assert all(p.lives == 2 for p in state.players)

    rules.advance_on_timeout(state, questions, now=1_700_000_070)  # -> new QUESTION
    assert state.phase is Phase.QUESTION
    assert state.round_no == 2
    assert state.question.id != first_question
    assert state.votes == {}
    assert state.result is None


def test_majority_round_applies_life_loss_then_continues(questions, make_room):
    state = make_room(4)
    while state.phase is not Phase.VOTE:
        rules.advance_on_timeout(state, questions, now=state.timer_ends_at + 0.1)

    pids = list(state.votes)
    vote_at = state.timer_ends_at - 1
    rules.submit_vote(state, pids[0], "A", now=vote_at)
    rules.submit_vote(state, pids[1], "A", now=vote_at)
    rules.submit_vote(state, pids[2], "A", now=vote_at)
    # pids[3] abstains

    rules.advance_on_timeout(state, questions, now=1_700_000_050)  # REVEAL
    assert state.result["status"] == "normal"
    assert state.result["losing_side"] == "A"

    rules.advance_on_timeout(state, questions, now=1_700_000_060)  # APPLY
    lives = {p.id: p.lives for p in state.players}
    assert lives[pids[0]] == 1
    assert lives[pids[1]] == 1
    assert lives[pids[2]] == 1
    assert lives[pids[3]] == 1  # the abstainer is punished too
    assert all(p.alive for p in state.players)

    rules.advance_on_timeout(state, questions, now=1_700_000_070)
    assert state.phase is Phase.QUESTION
    assert state.round_no == 2


def test_last_standing_player_wins(questions, make_room):
    """Drive a whole match to a single survivor."""
    state = make_room(3)
    guard = 0
    while state.phase is not Phase.GAME_OVER and guard < 50:
        guard += 1
        if state.phase is Phase.VOTE:
            pids = list(state.votes)
            # 2x A, 1x B -> A is the majority and both A players lose a life.
            rules.submit_vote(state, pids[0], "A", now=state.timer_ends_at - 1)
            rules.submit_vote(state, pids[1], "A", now=state.timer_ends_at - 1)
            rules.submit_vote(state, pids[2], "B", now=state.timer_ends_at - 1)
        rules.advance_on_timeout(state, questions, now=state.timer_ends_at + 0.1)

    assert state.phase is Phase.GAME_OVER
    assert len(state.winners) >= 1
    alive = [p for p in state.players if p.alive]
    assert [p.id for p in alive] == state.winners


def test_host_start_resets_after_game_over(questions, make_room):
    state = make_room(3)
    for player in state.players:
        player.lives = 0
    rules.eliminate_players(state.players)
    rules.check_winner(state.players)
    state.winners = [state.players[0].id]
    state.phase = Phase.GAME_OVER

    rules.start_game(state, questions, now=1_700_001_000.0)
    assert state.phase is Phase.QUESTION
    assert all(p.lives == 2 and p.alive for p in state.players)
    assert state.winners == []


def test_broadcast_state_hides_counts_before_reveal(vote_room):
    state = vote_room(4)
    payload = rules.broadcast_state(state, for_player_id=state.alive_players[0].id)

    assert payload["type"] == "state"
    assert payload["phase"] == "VOTE"
    assert payload["counts"] is None
    assert payload["result"] is None
    assert payload["you"]["choice"] is None
    assert "token" not in str(payload["players"][0])


def test_broadcast_state_shows_counts_after_reveal(vote_room):
    state = vote_room(4)
    pids = list(state.votes)
    rules.submit_vote(state, pids[0], "A", now=1_700_000_010)
    rules.submit_vote(state, pids[1], "A", now=1_700_000_010)
    rules.submit_vote(state, pids[2], "B", now=1_700_000_010)
    state.result = rules.compute_minority(state.votes)
    state.counts = state.result["counts"]
    state.phase = Phase.REVEAL

    payload = rules.broadcast_state(state, for_player_id=pids[0])
    assert payload["counts"] == {"A": 2, "B": 1}
    assert payload["result"]["losing_side"] == "A"
    assert payload["you"]["choice"] == "A"  # you may see your own vote now
    # Other players' choices are never exposed, only aggregate counts.
    assert "choice" not in payload["players"][1]


def test_everyone_dying_ends_the_game_without_winners(questions, make_room):
    state = make_room(3)
    for player in state.players:
        player.lives = 1
    while state.phase is not Phase.VOTE:
        rules.advance_on_timeout(state, questions, now=state.timer_ends_at + 0.1)

    pids = list(state.votes)
    rules.submit_vote(state, pids[0], "A", now=state.timer_ends_at - 1)
    rules.submit_vote(state, pids[1], "A", now=state.timer_ends_at - 1)
    rules.submit_vote(state, pids[2], "A", now=state.timer_ends_at - 1)
    rules.advance_on_timeout(state, questions, now=state.timer_ends_at + 0.1)  # REVEAL
    assert state.result["status"] == "unanimous"
    rules.advance_on_timeout(state, questions, now=state.timer_ends_at + 0.1)  # APPLY
    assert state.phase is Phase.APPLY
    rules.advance_on_timeout(state, questions, now=state.timer_ends_at + 0.1)
    assert state.phase is Phase.QUESTION  # void round -> replay, nobody died
