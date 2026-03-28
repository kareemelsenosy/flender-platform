"""FLENDER Platform — FastAPI application."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.config import SECRET_KEY, BASE_DIR
from app.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    # Restore persisted batch state from before any server restart
    try:
        from app.services.task_state import restore_on_startup
        from app.routers import sheets_routes
        batch_patch, user_patch = restore_on_startup()
        sheets_routes._batch_progress.update(batch_patch)
        for uid, bids in user_patch.items():
            sheets_routes._user_batches.setdefault(uid, []).extend(bids)
    except Exception:
        pass

    # L4: Clean up expired generated files from disk on startup
    try:
        from app.database import SessionLocal
        from app.routers.generate_routes import cleanup_expired_files
        _db = SessionLocal()
        try:
            n = cleanup_expired_files(_db)
            if n:
                import logging
                logging.getLogger(__name__).info(f"Cleaned up {n} expired generated files on startup")
        finally:
            _db.close()
    except Exception:
        pass

    yield


app = FastAPI(title="FLENDER Platform", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")

templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))

# Register routers
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

app.include_router(api_routes.router)
app.include_router(auth_routes.router)
app.include_router(upload_routes.router)
app.include_router(mapping_routes.router)
app.include_router(search_routes.router)
app.include_router(review_routes.router)
app.include_router(generate_routes.router)
app.include_router(sheets_routes.router)
app.include_router(settings_routes.router)
