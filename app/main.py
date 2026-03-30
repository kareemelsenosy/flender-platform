"""FLENDER Platform — FastAPI application."""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import SECRET_KEY, BASE_DIR
from app.database import init_db

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("flender")


# ── ASGI Middleware: security headers + static cache ─────────────────────────
class SecurityHeadersMiddleware:
    """Add security + cache headers to every response."""
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        is_static = scope["path"].startswith("/static/")

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                # Security headers on all responses
                headers.append((b"x-content-type-options", b"nosniff"))
                headers.append((b"x-frame-options", b"SAMEORIGIN"))
                headers.append((b"x-xss-protection", b"1; mode=block"))
                headers.append((b"referrer-policy", b"strict-origin-when-cross-origin"))
                headers.append((b"permissions-policy", b"camera=(), microphone=(), geolocation=()"))
                # HSTS — tell browsers to always use HTTPS
                headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                # Cache static files for 7 days; HTML pages no-cache for instant nav
                if is_static:
                    headers.append((b"cache-control", b"public, max-age=604800, immutable"))
                else:
                    headers.append((b"cache-control", b"no-cache"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


# ── Lifespan ─────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Database initialized")

    # Restore persisted batch state from before any server restart
    try:
        from app.services.task_state import restore_on_startup
        from app.routers import sheets_routes
        batch_patch, user_patch = restore_on_startup()
        sheets_routes._batch_progress.update(batch_patch)
        for uid, bids in user_patch.items():
            sheets_routes._user_batches.setdefault(uid, []).extend(bids)
        if batch_patch:
            logger.info(f"Restored {len(batch_patch)} batch(es) from disk")
    except Exception:
        pass

    # Clean up expired generated files from disk on startup
    try:
        from app.database import SessionLocal
        from app.routers.generate_routes import cleanup_expired_files
        _db = SessionLocal()
        try:
            n = cleanup_expired_files(_db)
            if n:
                logger.info(f"Cleaned up {n} expired generated files on startup")
        finally:
            _db.close()
    except Exception:
        pass

    logger.info("FLENDER Platform ready")
    yield
    logger.info("FLENDER Platform shutting down")


# ── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(title="FLENDER Platform", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, https_only=True)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")

templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))


# ── Request logging middleware ───────────────────────────────────────────────
@app.middleware("http")
async def request_logging(request: Request, call_next):
    start = time.time()
    try:
        response = await call_next(request)
    except Exception:
        duration = time.time() - start
        logger.error(f"{request.method} {request.url.path} — 500 ({duration:.2f}s)", exc_info=True)
        raise
    duration = time.time() - start
    # Only log slow requests or errors to keep logs clean
    if duration > 1.0 or response.status_code >= 400:
        logger.info(f"{request.method} {request.url.path} — {response.status_code} ({duration:.2f}s)")
    return response


# ── Global error handlers ────────────────────────────────────────────────────
@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "Not found"}, status_code=404)
    return HTMLResponse(
        templates.get_template("error.html").render(
            request=request, status_code=404, message="Page not found"
        ),
        status_code=404,
    )


@app.exception_handler(500)
async def server_error_handler(request: Request, exc):
    logger.error(f"Internal error on {request.url.path}: {exc}", exc_info=True)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "Internal server error"}, status_code=500)
    return HTMLResponse(
        templates.get_template("error.html").render(
            request=request, status_code=500, message="Something went wrong"
        ),
        status_code=500,
    )


# ── Register routers ────────────────────────────────────────────────────────
from app.routers import (  # noqa: E402
    api_routes,
    auth_routes,
    upload_routes,
    mapping_routes,
    search_routes,
    review_routes,
    generate_routes,
    sheets_routes,
    settings_routes,
)
from app.routers import _reset_route  # TEMPORARY — delete after use

app.include_router(api_routes.router)
app.include_router(auth_routes.router)
app.include_router(upload_routes.router)
app.include_router(mapping_routes.router)
app.include_router(search_routes.router)
app.include_router(review_routes.router)
app.include_router(generate_routes.router)
app.include_router(sheets_routes.router)
app.include_router(settings_routes.router)
app.include_router(_reset_route.router)  # TEMPORARY
