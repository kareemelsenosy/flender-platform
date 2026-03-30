"""TEMPORARY: one-time DB reset endpoint. DELETE THIS FILE after use."""
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.database import SessionLocal
from app.models import User, Session, UploadedFile, UniqueItem, SearchCache, ColumnMappingFormat, BrandSearchConfig, GeneratedFile

router = APIRouter()
_RESET_TOKEN = "flender-reset-2026-x7k9"

@router.post("/admin/reset-all-data")
async def reset_all_data(x_reset_token: str = Header(None)):
    if x_reset_token != _RESET_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")
    db = SessionLocal()
    try:
        counts = {}
        counts['generated_files'] = db.query(GeneratedFile).delete()
        counts['search_cache']    = db.query(SearchCache).delete()
        counts['unique_items']    = db.query(UniqueItem).delete()
        counts['uploaded_files']  = db.query(UploadedFile).delete()
        counts['sessions']        = db.query(Session).delete()
        counts['brand_configs']   = db.query(BrandSearchConfig).delete()
        counts['mapping_formats'] = db.query(ColumnMappingFormat).delete()
        counts['users']           = db.query(User).delete()
        db.commit()
        return JSONResponse({"cleared": True, "counts": counts})
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
