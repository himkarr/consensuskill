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
from typing import Any

from fastapi import FastAPI, Header, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from gateway.app import ws as ws_module
from gateway.app.models import ConnectionRegistry

logger = logging.getLogger(__name__)

DEFAULT_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def default_instance_id() -> str:
    return os.environ.get("INSTANCE_ID") or f"gateway-{uuid.uuid4().hex[:8]}"


def create_app(
    redis_client: Redis | None = None,
    instance_id: str | None = None,
    *,
    embedded_engine: bool | None = None,
    questions: list | None = None,
    allow_origins: list[str] | None = None,
) -> FastAPI:
    """Build the gateway app.

    ``embedded_engine=True`` also runs the round engine in this process. It is
    used by the test-suite (one in-process stack against fakeredis), by
    ``EMBEDDED_ENGINE=1`` local runs, and by free-tier deploys: Render's free
    plan fits exactly one web service, so gateway + engine share the process
    and its 750 instance-hours/month.
    """
    instance = instance_id or default_instance_id()
    if embedded_engine is None:
        embedded_engine = os.environ.get("EMBEDDED_ENGINE", "").lower() in {"1", "true", "yes"}

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
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:  # noqa: BLE001
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
    async def root() -> dict[str, Any]:
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

    return app


app = create_app()
