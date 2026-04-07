#!/bin/bash
set -e

# Ensure tables exist (no-op if they already do)
python -c "from app.database import init_db; init_db()"

# Run Alembic migrations
# - If alembic_version doesn't exist yet, stamp base first
python -c "
from app.database import engine
from sqlalchemy import inspect, text
insp = inspect(engine)
if 'alembic_version' not in insp.get_table_names():
    print('First run: stamping alembic base...')
    with engine.begin() as conn:
        conn.execute(text(\"\"\"CREATE TABLE IF NOT EXISTS alembic_version (
            version_num VARCHAR(32) NOT NULL,
            CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
        )\"\"\"))
"

alembic upgrade head || echo "Migration warning (may be first run)"

exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1
