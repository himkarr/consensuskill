"""Static SPA serving (``STATIC_DIR``) and the WebSocket keepalive.

The Azure Container Apps deploy runs a single container: FastAPI answers the
SPA, the probe path and the socket on one port. These tests pin that contract -
most importantly that the catch-all route does not swallow ``/ws`` or
``/health``.
"""

from __future__ import annotations

import fakeredis.aioredis as fr
from fastapi.testclient import TestClient

from gateway.app.main import create_app

INDEX = (
    "<!doctype html><html><head><title>ConsensusKill</title></head>"
    "<body><div id=root></div></body></html>"
)


def make_static_client(tmp_path, *, ws_ping_seconds: float = 0) -> TestClient:
    (tmp_path / "index.html").write_text(INDEX, encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app-abc123.js").write_text("export const x = 1", encoding="utf-8")

    app = create_app(
        redis_client=fr.FakeRedis(),
        instance_id="gw-static",
        embedded_engine=False,
        static_dir=str(tmp_path),
        ws_ping_seconds=ws_ping_seconds,
    )
    return TestClient(app)


def test_root_serves_index(tmp_path):
    with make_static_client(tmp_path) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "ConsensusKill" in response.text


def test_unknown_route_falls_back_to_index(tmp_path):
    """The app is one page: a reload on any URL must still boot the SPA."""
    with make_static_client(tmp_path) as client:
        response = client.get("/some/deep/link")
    assert response.status_code == 200
    assert "ConsensusKill" in response.text


def test_assets_are_served(tmp_path):
    with make_static_client(tmp_path) as client:
        response = client.get("/assets/app-abc123.js")
    assert response.status_code == 200
    assert "export const x" in response.text


def test_path_traversal_is_refused(tmp_path):
    secret = tmp_path.parent / "secret.txt"
    secret.write_text("do not serve me", encoding="utf-8")
    try:
        with make_static_client(tmp_path) as client:
            response = client.get("/../secret.txt")
        assert "do not serve me" not in response.text
    finally:
        secret.unlink(missing_ok=True)


def test_api_routes_survive_the_catch_all(tmp_path):
    """The SPA mount is registered last so it cannot shadow the API."""
    with make_static_client(tmp_path) as client:
        health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["service"] == "gateway"


def test_websocket_still_upgrades(tmp_path):
    with make_static_client(tmp_path) as client, client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
    assert hello["type"] == "instance_info"


def test_missing_index_disables_spa(tmp_path):
    app = create_app(
        redis_client=fr.FakeRedis(),
        instance_id="gw-static",
        embedded_engine=False,
        static_dir=str(tmp_path),
        ws_ping_seconds=0,
    )
    with TestClient(app) as client:
        response = client.get("/")
    # No bundle present: the plain JSON root is served, not a crash.
    assert response.json()["service"] == "gateway"