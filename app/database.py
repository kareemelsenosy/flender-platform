"""SQLAlchemy database setup — supports SQLite (dev) and PostgreSQL (production)."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import DATABASE_URL

_is_sqlite = DATABASE_URL.startswith("sqlite")
_is_postgres = DATABASE_URL.startswith("postgresql") or DATABASE_URL.startswith("postgres")

# Normalize postgres:// → postgresql:// (Heroku/Railway use the older scheme)
_url = DATABASE_URL
if _url.startswith("postgres://"):
    _url = "postgresql://" + _url[len("postgres://"):]

connect_args = {"check_same_thread": False} if _is_sqlite else {}
engine_kwargs: dict = {"connect_args": connect_args}
if _is_postgres:
    # Connection pool tuned for production; SQLite doesn't support pool settings
    # pool_size=20 to handle 20 search workers + web requests
    # pool_recycle=900 avoids aggressive reconnection churn
    engine_kwargs.update({
        "pool_size": 20,
        "max_overflow": 10,
        "pool_pre_ping": True,
        "pool_recycle": 900,
    })

engine = create_engine(_url, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables and run lightweight migrations for new columns."""
    Base.metadata.create_all(bind=engine)
    _run_migrations()


def _run_migrations():
    """Add columns that may be missing from existing tables."""
    from sqlalchemy import text, inspect
    insp = inspect(engine)
    with engine.begin() as conn:
        # Add additional_urls_json to unique_items if missing
        if "unique_items" in insp.get_table_names():
            cols = {c["name"] for c in insp.get_columns("unique_items")}
            if "additional_urls_json" not in cols:
                conn.execute(text(
                    "ALTER TABLE unique_items ADD COLUMN additional_urls_json TEXT DEFAULT '[]'"
                ))
            if "barcode" not in cols:
                conn.execute(text(
                    "ALTER TABLE unique_items ADD COLUMN barcode VARCHAR(255)"
                ))
