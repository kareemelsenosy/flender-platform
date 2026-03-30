"""TEMPORARY: verify DB is empty. DELETE after use."""
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from app.database import SessionLocal
from app.models import User, Session, UniqueItem, SearchCache

router = APIRouter()
_TOKEN = "flender-verify-2026"

@router.get("/admin/verify-empty")
async def verify_empty(x_verify_token: str = Header(None)):
    if x_verify_token != _TOKEN:
        raise HTTPException(status_code=403)
    db = SessionLocal()
    try:
        return JSONResponse({
            "users": db.query(User).count(),
            "sessions": db.query(Session).count(),
            "unique_items": db.query(UniqueItem).count(),
            "search_cache": db.query(SearchCache).count(),
            "all_users": [{"id": u.id, "username": u.username, "email": u.email} for u in db.query(User).all()],
        })
    finally:
        db.close()
