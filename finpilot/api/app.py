"""FinPilot application factory. HTTP owns neither household state nor money."""
from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import hashlib
import os
from pathlib import Path
import time
import uuid
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy.exc import OperationalError

from ..persistence.database import Database
from ..runtime import Runtime, RevisionConflict
from ..services.auth import AuthError
from .auth_routes import router as auth_router
from .finance_routes import router as finance_router
from .workspace_routes import router as workspace_router
from .assistant_routes import router as assistant_router
from .bank_routes import router as bank_router
from .conversation_routes import router as conversation_router
from .document_routes import router as document_router
from .mcp_routes import router as mcp_router
from .action_routes import router as action_router
from .route_docs import apply_route_docs
from ..services.conversations import ConversationBusy

WEB = Path(__file__).resolve().parent.parent / "web"
log = logging.getLogger("finpilot.requests")


class BodyLimitMiddleware:
    def __init__(self, app, limit=2_100_000):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        total = 0
        async def bounded_receive():
            nonlocal total
            message = await receive()
            total += len(message.get("body", b""))
            if total > self.limit:
                from starlette.exceptions import HTTPException
                raise HTTPException(413, "Request is too large. Import at most 2 MB at a time.")
            return message
        await self.app(scope, bounded_receive, send)


def create_app(database_url=None, *, llm=None):
    runtime = Runtime(Database(database_url), llm)
    # Version the entire relative-import tree together. Updating only app.js
    # leaves a browser free to reuse an older cached assistant.js or core.js.
    asset_digest = hashlib.sha256()
    for asset in sorted(WEB.iterdir()):
        if asset.is_file() and asset.suffix in {".js", ".css", ".svg"}:
            asset_digest.update(asset.name.encode("utf-8"))
            asset_digest.update(asset.read_bytes())
    asset_prefix = "/assets/v" + asset_digest.hexdigest()[:16]
    production = os.getenv("FINPILOT_ENV") == "production"
    origin = os.getenv("FINPILOT_PUBLIC_ORIGIN", "").rstrip("/")
    if production and (not origin.startswith("https://") or not urlparse(origin).hostname):
        raise RuntimeError("Set FINPILOT_PUBLIC_ORIGIN to the hosted HTTPS origin.")

    @asynccontextmanager
    async def lifespan(app):
        runtime.initialize()
        runtime.start_model()
        yield
        runtime.close()

    app = FastAPI(title="FinPilot", version="2.0", lifespan=lifespan,
        description="Authenticated financial workspaces with deterministic calculations.")
    app.state.runtime = runtime
    app.state.public_origin = origin
    app.state.secure_cookies = production or origin.startswith("https://")
    hosts = [urlparse(origin).hostname] if production else ["localhost", "127.0.0.1", "testserver"]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    app.add_middleware(BodyLimitMiddleware)

    @app.middleware("http")
    async def observability(request: Request, call_next):
        start, request_id = time.perf_counter(), uuid.uuid4().hex
        response = await call_next(request)
        elapsed = (time.perf_counter() - start) * 1000
        response.headers["Server-Timing"] = f"app;dur={elapsed:.1f}"
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "no-cache"
        if production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if hasattr(request.state, "revision"):
            response.headers["X-Workspace-Revision"] = str(request.state.revision)
        # Never log credentials, questions, amounts, query strings or request bodies.
        log.info("%s %s status=%s duration_ms=%.1f request_id=%s",
                 request.method, request.url.path, response.status_code, elapsed, request_id)
        return response

    @app.exception_handler(AuthError)
    async def auth_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.exception_handler(RevisionConflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(ConversationBusy)
    async def conversation_busy(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(ValueError)
    async def invalid_input(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(KeyError)
    async def missing_record(request, exc):
        return JSONResponse({"detail": "The requested record was not found in this workspace."}, status_code=404)

    @app.exception_handler(OperationalError)
    async def database_unavailable(request, exc):
        log.error("Database operation failed: %s", type(exc).__name__)
        return JSONResponse({"detail": "The workspace is busy. Please retry shortly."}, status_code=503)

    @app.get("/api/health")
    def health():
        from sqlalchemy import text
        runtime.initialize()
        with runtime.db.sessions() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ok", "version": "2.0", "storage": runtime.db.engine.dialect.name}

    @app.get("/", response_class=HTMLResponse)
    def dashboard():
        html = (WEB / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html.replace('/assets/', asset_prefix + '/'),
                            headers={"Cache-Control": "no-cache"})

    app.include_router(auth_router)
    app.include_router(finance_router)
    app.include_router(workspace_router)
    app.include_router(assistant_router)
    app.include_router(bank_router)
    app.include_router(conversation_router)
    app.include_router(document_router)
    app.include_router(mcp_router)
    app.include_router(action_router)
    apply_route_docs(app)
    app.mount(asset_prefix, StaticFiles(directory=WEB), name="versioned-assets")
    app.mount("/assets", StaticFiles(directory=WEB), name="assets")
    return app


app = create_app()
