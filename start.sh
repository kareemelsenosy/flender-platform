#!/bin/bash
set -e

echo "=== Starting FLENDER Platform ==="

# Prepare database: create tables if fresh, stamp alembic if unversioned
python3 - <<'PYEOF'
from app.database import engine
from sqlalchemy import inspect, text

insp = inspect(engine)
tables = insp.get_table_names()
print(f"Existing tables: {tables}")

# Fresh DB — create all tables via SQLAlchemy
if "users" not in tables:
    print("Fresh database — creating all tables via SQLAlchemy...")
    from app.database import Base
    import app.models  # register all models
    Base.metadata.create_all(engine)
    print("Tables created.")

# Ensure alembic_version table exists
alembic_exists = "alembic_version" in inspect(engine).get_table_names()
if not alembic_exists:
    print("Creating alembic_version table...")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS alembic_version (
                version_num VARCHAR(32) NOT NULL,
                CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
            )
        """))

# If alembic_version is empty, stamp at head so alembic doesn't re-run
# migrations that SQLAlchemy's create_all already applied.
with engine.begin() as conn:
    rows = conn.execute(text("SELECT version_num FROM alembic_version")).fetchall()
    if not rows:
        print("alembic_version is empty — stamping at head to skip already-applied migrations...")
        # Import config to find head revision
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "stamp", "head"],
            capture_output=True, text=True
        )
        print(result.stdout)
        if result.returncode != 0:
            print(result.stderr)
            raise RuntimeError("alembic stamp failed")
        print("Stamped at head.")
PYEOF

echo "Running alembic migrations..."
alembic upgrade head
echo "Migrations complete."

echo "Starting uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1
