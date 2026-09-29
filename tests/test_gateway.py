"""Gateway WebSocket tests (spec tests 1, 2, 8 through real sockets).

The gateway runs with an embedded round engine against fakeredis, so a test
message really does travel: socket -> ingest stream -> engine -> Pub/Sub ->
socket.
"""

from __future__ import annotations

import fakeredis.aioredis as fr
from fastapi.testclient import TestClient

from gateway.app.main import create_app
from shared.constants import PHASE_DURATIONS
from shared.questions import load_json_bank
from shared.schemas import Phase


def make_client(instance_id: str = "gw-test") -> TestClient:
    redis = fr.FakeRedis()
    app = create_app(
        redis_client=redis,
        instance_id=instance_id,
        embedded_engine=True,
        questions=load_json_bank(),
    )
    return TestClient(app)


def recv(ws, kind: str, limit: int = 60) -> dict:
    """Read messages until one of ``kind`` shows up."""
    for _ in range(limit):
        message = ws.receive_json()
        if message.get("type") == kind:
            return message
    raise AssertionError(f"did not receive a {kind!r} message")


def recv_state(ws, limit: int = 60) -> dict:
    return recv(ws, "state", limit)


def recv_phase(ws, phase: str, limit: int = 60) -> dict:
    """Read states until the room reaches ``phase``."""
    for _ in range(limit):
        state = recv_state(ws)
        if state.get("phase") == phase:
            return state
    raise AssertionError(f"room never reached phase {phase}")


class Lobby:
    """A set of open sockets forming one room (host first)."""

    def __init__(self, client: TestClient, players: int = 4) -> None:
        self.client = client
        self.cms = []
        self.sockets = []
        self.joined = []
        for _ in range(players):
            cm = client.websocket_connect("/ws")
            ws = cm.__enter__()
            self.cms.append(cm)
            self.sockets.append(ws)
            ws.receive_json()  # instance_info

        self.sockets[0].send_json({"type": "create_room", "nickname": "host"})
        self.joined.append(recv(self.sockets[0], "joined"))
        self.code = self.joined[0]["room_code"]

        for index, ws in enumerate(self.sockets[1:], start=1):
            ws.send_json({"type": "join", "code": self.code, "nickname": f"p{index}"})
            self.joined.append(recv(ws, "joined"))

    @property
    def host(self):
        return self.sockets[0]

    @property
    def guests(self):
        return self.sockets[1:]

    def close(self) -> None:
        for cm in self.cms:
            try:
                cm.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass


# --- spec 1: health ---------------------------------------------------------
def test_gateway_health_returns_200():
    with make_client() as client:
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["service"] == "gateway"
        assert body["instance_id"] == "gw-test"
        assert body["redis"] is True


def test_engine_health_returns_200():
    from engine.engine import create_engine_app

    app = create_engine_app(redis_client=fr.FakeRedis(), run_loops=False, instance_id="eng-1")
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["service"] == "engine"


# --- spec 2: joining --------------------------------------------------------
def test_create_and_join_returns_unique_ids_and_tokens():
    with make_client() as client:
        lobby = Lobby(client, players=5)
        try:
            ids = [message["player_id"] for message in lobby.joined]
            tokens = [message["token"] for message in lobby.joined]
            assert len(ids) == 5 and len(set(ids)) == 5
            assert len(set(tokens)) == 5
            assert lobby.joined[0]["is_host"] is True
            assert all(not message["is_host"] for message in lobby.joined[1:])
        finally:
            lobby.close()


def test_socket_receives_instance_badge():
    with make_client() as client:
        with client.websocket_connect("/ws") as ws:
            info = ws.receive_json()
            assert info["type"] == "instance_info"
            assert info["instance_id"] == "gw-test"
            assert info["server_time"] > 0


def test_duplicate_nickname_rejected_over_ws():
    with make_client() as client:
        lobby = Lobby(client, players=2)
        try:
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()
                ws.send_json({"type": "join", "code": lobby.code, "nickname": "HOST"})
                error = recv(ws, "error")
                assert error["code"] == "nickname_taken"
        finally:
            lobby.close()


def test_unknown_room_rejected():
    with make_client() as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "join", "code": "ZZZZZZ", "nickname": "bob"})
            error = recv(ws, "error")
            assert error["code"] == "room_not_found"


def test_malformed_json_is_answered_with_an_error():
    with make_client() as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_text("{not json")
            error = recv(ws, "error")
            assert error["code"] == "bad_message"


# --- state payload ----------------------------------------------------------
def test_state_messages_carry_you_and_hide_votes():
    with make_client() as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "create_room", "nickname": "host"})
            joined = recv(ws, "joined")
            state = recv_state(ws)
            assert state["phase"] == "LOBBY"
            assert state["you"]["player_id"] == joined["player_id"]
            assert state["you"]["is_host"] is True
            assert state["counts"] is None
            assert state["votes"] is None
            assert "token" not in str(state["players"][0])


# --- host powers ------------------------------------------------------------
def test_host_can_start_and_non_host_cannot():
    with make_client() as client:
        lobby = Lobby(client, players=3)
        try:
            lobby.guests[0].send_json({"type": "host_start"})
            error = recv(lobby.guests[0], "error")
            assert error["code"] == "not_host"

            lobby.host.send_json({"type": "host_start"})
            state = recv_phase(lobby.host, "QUESTION")
            assert state["phase"] == "QUESTION"
            assert state["question"]["text"]
            assert state["round_no"] == 1
        finally:
            lobby.close()


def test_start_requires_three_players():
    with make_client() as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "create_room", "nickname": "host"})
            recv(ws, "joined")
            ws.send_json({"type": "host_start"})
            error = recv(ws, "error")
            assert error["code"] == "not_enough_players"


def test_host_kick_in_lobby():
    with make_client() as client:
        lobby = Lobby(client, players=3)
        try:
            target = lobby.joined[2]["player_id"]
            lobby.host.send_json({"type": "host_kick", "player_id": target})
            # The kicked socket receives an error, everyone gets a new state.
            error = recv(lobby.sockets[2], "error")
            assert error["code"] == "kicked"
            state = None
            seen_target = False
            for _ in range(20):
                state = recv_state(lobby.host)
                ids = {p["id"] for p in state["players"]}
                if target in ids:
                    seen_target = True
                elif seen_target:
                    break
            assert state is not None and seen_target
            assert target not in {p["id"] for p in state["players"]}
            assert len(state["players"]) == 2
        finally:
            lobby.close()


# --- spec 4/8 over sockets --------------------------------------------------
def test_vote_in_wrong_phase_rejected_over_ws():
    with make_client() as client:
        lobby = Lobby(client, players=4)
        try:
            lobby.host.send_json({"type": "host_start"})
            recv_phase(lobby.host, "QUESTION")
            lobby.guests[0].send_json({"type": "vote", "choice": "A"})
            error = recv(lobby.guests[0], "error")
            assert error["code"] == "vote_wrong_phase"
        finally:
            lobby.close()


def test_chat_and_rate_limit_over_ws():
    with make_client() as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "create_room", "nickname": "host"})
            recv(ws, "joined")

            ws.send_json({"type": "chat", "text": "  hello   world  "})
            chat = recv(ws, "chat")
            assert chat["text"] == "hello world"

            ws.send_json({"type": "chat", "text": "spam"})
            error = recv(ws, "error")
            assert error["code"] == "rate_limited"


def test_chat_disabled_during_vote_phase(monkeypatch):
    monkeypatch.setitem(PHASE_DURATIONS, Phase.QUESTION, 0.3)
    monkeypatch.setitem(PHASE_DURATIONS, Phase.DISCUSSION, 0.3)

    with make_client() as client:
        lobby = Lobby(client, players=3)
        try:
            lobby.host.send_json({"type": "host_start"})
            recv_phase(lobby.host, "VOTE")

            lobby.guests[0].send_json({"type": "chat", "text": "sneaky"})
            error = recv(lobby.guests[0], "error")
            assert error["code"] == "chat_disabled"
        finally:
            lobby.close()


# --- spec 7: reconnect ------------------------------------------------------
def test_reconnect_with_token_restores_player():
    with make_client() as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "create_room", "nickname": "host"})
            joined = recv(ws, "joined")
            token = joined["token"]
            player_id = joined["player_id"]

        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "reconnect", "token": token})
            again = recv(ws, "joined")
            assert again["player_id"] == player_id
            assert again["token"] == token
            state = recv_state(ws)
            assert state["you"]["lives"] == 2
            assert state["you"]["alive"] is True
            assert len(state["players"]) == 1  # reconnect never duplicates


def test_reconnect_with_bad_token_rejected():
    with make_client() as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "reconnect", "token": "bogus"})
            error = recv(ws, "error")
            assert error["code"] == "invalid_token"


# --- fault tolerance hook ---------------------------------------------------
def test_non_host_cannot_kill_the_gateway():
    with make_client() as client:
        lobby = Lobby(client, players=3)
        try:
            lobby.guests[0].send_json({"type": "kill_instance"})
            error = recv(lobby.guests[0], "error")
            assert error["code"] == "not_host"
        finally:
            lobby.close()
