"""Spec tests 3, 5 and 6: minority computation, life loss, winners."""

from __future__ import annotations

from engine import rules


# --- spec 3: compute_minority ---------------------------------------------
def test_majority_loses_seven_vs_three():
    votes = {f"p{i}": "A" for i in range(7)}
    votes.update({f"q{i}": "B" for i in range(3)})

    result = rules.compute_minority(votes)

    assert result["status"] == "normal"
    assert result["counts"] == {"A": 7, "B": 3}
    # The minority side survives, the majority side is the losing side.
    assert result["losing_side"] == "A"
    assert result["minority_side"] == "B"
    assert len(result["survivors"]) == 3
    assert len(result["losers"]) == 7
    assert all(pid.startswith("q") for pid in result["survivors"])


def test_mirror_split_minority_on_a():
    votes = {"a": "B", "b": "B", "c": "A"}
    result = rules.compute_minority(votes)
    assert result["status"] == "normal"
    assert result["losing_side"] == "B"
    assert result["survivors"] == ["c"]


def test_tie_returns_tie_status():
    votes = {"a": "A", "b": "B", "c": "A", "d": "B"}
    result = rules.compute_minority(votes)
    assert result["status"] == "tie"
    assert result["losing_side"] is None
    assert result["losers"] == []
    assert result["counts"] == {"A": 2, "B": 2}


def test_unanimous_returns_unanimous_status():
    votes = {f"p{i}": "A" for i in range(5)}
    result = rules.compute_minority(votes)
    assert result["status"] == "unanimous"
    assert result["losing_side"] is None
    assert result["losers"] == []


def test_nobody_voted_is_void():
    result = rules.compute_minority({"a": None, "b": None})
    assert result["status"] == "unanimous"
    assert result["counts"] == {"A": 0, "B": 0}
    assert result["losers"] == []


def test_abstainers_cannot_void_a_one_sided_vote():
    votes = {"a": "A", "b": "A", "c": None}
    result = rules.compute_minority(votes)
    # Two cast votes for A, none for B, but somebody abstained -> the round is
    # decisive and the abstainer is punished alongside the majority.
    assert result["status"] == "normal"
    assert result["counts"] == {"A": 2, "B": 0}
    assert result["abstainers"] == ["c"]
    assert set(result["losers"]) == {"a", "b", "c"}
    assert result["survivors"] == []


def test_full_unanimous_vote_is_void_even_without_abstainers():
    votes = {"a": "B", "b": "B", "c": "B"}
    result = rules.compute_minority(votes)
    assert result["status"] == "unanimous"
    assert result["losers"] == []


# --- spec 5: a player who does not vote loses a life -----------------------
def test_no_vote_player_loses_a_life(vote_room):
    state = vote_room(4)
    voters = list(state.votes)
    afk = voters[-1]
    assert rules.submit_vote(state, voters[0], "A", now=1_700_000_010).accepted
    assert rules.submit_vote(state, voters[1], "A", now=1_700_000_010).accepted
    assert rules.submit_vote(state, voters[2], "B", now=1_700_000_010).accepted

    result = rules.compute_minority(state.votes)
    assert result["status"] == "normal"
    assert afk in result["losers"]

    lives_before = {p.id: p.lives for p in state.players}
    rules.apply_lives(state.players, result)
    rules.eliminate_players(state.players)
    after = {p.id: p.lives for p in state.players}

    assert after[afk] == lives_before[afk] - 1
    # Both A voters were the majority and also lose a life.
    assert after[voters[0]] == lives_before[voters[0]] - 1
    assert after[voters[1]] == lives_before[voters[1]] - 1
    # The single B voter is the minority and survives untouched.
    assert after[voters[2]] == lives_before[voters[2]]


def test_void_round_nobody_loses_a_life(vote_room):
    state = vote_room(4)
    for pid in state.votes:
        assert rules.submit_vote(state, pid, "B", now=1_700_000_010).accepted

    result = rules.compute_minority(state.votes)
    affected = rules.apply_lives(state.players, result)
    rules.eliminate_players(state.players)

    assert result["status"] == "unanimous"
    assert affected == []
    assert all(p.lives == 2 for p in state.players)


# --- spec 6: check_winner --------------------------------------------------
def test_check_winner_single_survivor():
    state_players = _players([("a", 1, True), ("b", 0, False), ("c", 0, False)])
    assert rules.check_winner(state_players) == ["a"]


def test_check_winner_two_alive_no_change_is_a_draw():
    state_players = _players([("a", 1, True), ("b", 1, True)])
    assert rules.check_winner(state_players, no_change=True) == ["a", "b"]
    assert rules.check_winner(state_players, no_change=False) == []


def test_check_winner_three_alive_continues():
    state_players = _players([("a", 1, True), ("b", 1, True), ("c", 1, True)])
    assert rules.check_winner(state_players) == []
    assert rules.check_winner(state_players, no_change=True) == []


def test_check_winner_nobody_alive():
    state_players = _players([("a", 0, False), ("b", 0, False)])
    assert rules.check_winner(state_players, no_change=True) == []


def _players(rows):
    from shared.schemas import Player

    return [Player(id=pid, nickname=pid, lives=lives, alive=alive) for pid, lives, alive in rows]
