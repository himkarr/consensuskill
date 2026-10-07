"""Gateway service: the stateless WebSocket front door.

Run it with: ``uvicorn gateway.app.main:app --port 8000``

Two gateways (or twenty) can sit behind a load balancer: none of them hold
game state, they only hold sockets. Every client action goes to a Redis
Stream, every state update comes back through Redis Pub/Sub.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from redis.asyncio import Redis

from gateway.app import ws as ws_module
from gateway.app.models import ConnectionRegistry

logger = logging.getLogger(__name__)

DEFAULT_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# WebSocket keepalive period. Cloud edges close sockets that go quiet, and a
# round sits idle for tens of seconds between phases, so the gateway pings on
# its own. 0 disables it (used by the test-suite, which drives sockets directly).
DEFAULT_WS_PING_SECONDS = float(os.environ.get("WS_PING_SECONDS", "30"))


def default_instance_id() -> str:
    return os.environ.get("INSTANCE_ID") or f"gateway-{uuid.uuid4().hex[:8]}"


def spa_root(static_dir: str | None) -> Path | None:
    """Resolve ``STATIC_DIR`` to its ``index.html``, or None if unusable."""
    if not static_dir:
        return None
    root = Path(static_dir).resolve()
    return root if (root / "index.html").is_file() else None


def create_app(
    redis_client: Redis | None = None,
    instance_id: str | None = None,
    *,
    embedded_engine: bool | None = None,
    questions: list | None = None,
    allow_origins: list[str] | None = None,
    static_dir: str | None = None,
    ws_ping_seconds: float | None = None,
) -> FastAPI:
    """Build the gateway app.

    ``embedded_engine=True`` also runs the round engine in this process. It is
    used by the test-suite (one in-process stack against fakeredis), by
    ``EMBEDDED_ENGINE=1`` local runs, and by single-process deploys: Azure
    Container Apps puts the gateway and the engine in one revision, so they
    share a process and its scaling.
    """
    instance = instance_id or default_instance_id()
    if embedded_engine is None:
        embedded_engine = os.environ.get("EMBEDDED_ENGINE", "").lower() in {"1", "true", "yes"}
    if ws_ping_seconds is None:
        ws_ping_seconds = DEFAULT_WS_PING_SECONDS
    if static_dir is None:
        static_dir = os.environ.get("STATIC_DIR", "")
    bundle = spa_root(static_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        redis = redis_client or Redis.from_url(DEFAULT_REDIS_URL, decode_responses=False)
        owns_redis = redis_client is None
        app.state.redis = redis
        app.state.registry = ConnectionRegistry()
        app.state.instance_id = instance

        tasks: list[asyncio.Task] = [
            asyncio.create_task(ws_module.relay_loop(app), name="pubsub-relay")
        ]
        if ws_ping_seconds and ws_ping_seconds > 0:
            tasks.append(
                asyncio.create_task(
                    ws_module.keepalive_loop(app, ws_ping_seconds), name="ws-keepalive"
                )
            )
        app.state.engine = None
        if embedded_engine:
            # Imported lazily so a production gateway never needs engine code.
            from engine.engine import RoundEngine
            from shared.questions import load_questions

            engine = RoundEngine(
                redis,
                questions or load_questions(),
                instance_id=f"{instance}-embedded",
            )
            await engine.start()
            app.state.engine = engine
            tasks.extend(
                [
                    asyncio.create_task(engine.run_consumer(), name="ingest-consumer"),
                    asyncio.create_task(engine.run_wake(), name="wake-subscriber"),
                    asyncio.create_task(engine.run_timer(), name="phase-timer"),
                ]
            )
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                try:
                    # Bounded wait: a task wedged in redis/pubsub teardown gets a
                    # second cancel here instead of holding the app open forever.
                    await asyncio.wait_for(task, timeout=3)
                except asyncio.CancelledError:
                    pass
                except Exception:  # noqa: BLE001 - TimeoutError etc: never block shutdown
                    pass
            if app.state.engine is not None:
                await app.state.engine.stop()
            if owns_redis:
                await redis.aclose()

    app = FastAPI(title="ConsensusKill Gateway", lifespan=lifespan)
    app.state.instance_id = instance

    origins = allow_origins or [
        origin.strip()
        for origin in os.environ.get("ALLOWED_ORIGINS", "*").split(",")
        if origin.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/")
    async def root() -> Any:
        # With a bundle present "/" is the SPA; without one it stays the
        # service banner (the local compose profile serves the SPA from nginx).
        if bundle is not None:
            return FileResponse(bundle / "index.html")
        return {"service": "gateway", "instance_id": instance, "docs": "/docs"}

    @app.get("/health")
    async def health() -> dict[str, Any]:
        redis: Redis | None = getattr(app.state, "redis", None)
        registry: ConnectionRegistry = getattr(app.state, "registry", None)
        redis_ok = False
        if redis is not None:
            try:
                redis_ok = bool(await redis.ping())
            except Exception:  # noqa: BLE001
                redis_ok = False
        return {
            "service": "gateway",
            "status": "ok",
            "instance_id": instance,
            "redis": redis_ok,
            "connections": registry.count if registry else 0,
            "rooms": registry.room_count if registry else 0,
            "time": time.time(),
        }

    @app.post("/admin/kill")
    async def admin_kill(request: Request, x_admin_token: str | None = Header(default=None)) -> Any:
        """Hard-kill this gateway process (fault-tolerance demo).

        Guarded by ``ADMIN_TOKEN`` when that env var is set.
        """
        expected = os.environ.get("ADMIN_TOKEN")
        if expected and x_admin_token != expected:
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        logger.critical("admin kill requested for gateway %s", instance)
        os._exit(1)  # noqa: S606 - deliberate crash for the demo
        return JSONResponse({"detail": "unreachable"})  # pragma: no cover

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket) -> None:
        await ws_module.handle_connection(app, ws)

    # Mounted last on purpose: a catch-all route registered earlier would
    # shadow /ws, /health and /admin/kill.
    mount_spa(app, static_dir)

    return app


def mount_spa(app: FastAPI, static_dir: str | None) -> None:
    """Serve the built React bundle from the gateway (optional).

    Set ``STATIC_DIR`` to the directory holding ``index.html``. Every unknown
    path falls back to ``index.html`` because the app is a single page, so deep
    links like ``/?code=ABC234`` and any future client route keep working.

    This is what lets a one-container deploy (Azure Container Apps) ship the
    whole product without a separate nginx hop: the same process answers
    ``GET /`` for the SPA, ``GET /health`` for the platform probe and
    ``WS /ws`` for the game.
    """
    root = spa_root(static_dir)
    if root is None:
        if static_dir:
            logger.warning("STATIC_DIR=%s has no index.html - not serving the SPA", static_dir)
        return

    index = root / "index.html"
    logger.info("serving SPA from %s", root)

    @app.get("/{spa_path:path}", include_in_schema=False)
    async def spa(spa_path: str) -> FileResponse:
        # Resolve inside the root only: never let `..` or a symlink escape it.
        candidate = (root / spa_path).resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)


app = create_app()
