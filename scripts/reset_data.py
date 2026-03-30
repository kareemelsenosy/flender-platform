"""One-time script: delete all data, keep schema. Run via: python scripts/reset_data.py"""
from __future__ import annotations
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, engine
from app.models import Base, User, Session, UploadedFile, UniqueItem, SearchCache, ColumnMappingFormat, BrandSearchConfig, GeneratedFile
from sqlalchemy import text

def reset():
    db = SessionLocal()
    try:
        # Delete in FK-safe order (children first)
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
        print("✓ Database cleared:")
        for table, n in counts.items():
            print(f"  {table}: {n} rows deleted")
    except Exception as e:
        db.rollback()
        print(f"✗ Error: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    print("Resetting database (structure preserved)...")
    reset()
    print("Done.")
