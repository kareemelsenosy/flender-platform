#!/bin/bash
set -e

echo "=== Starting FLENDER Platform ==="

# Run Alembic migrations (handles both fresh installs and upgrades)
python -c "
from app.database import engine
from sqlalchemy import inspect, text

insp = inspect(engine)
tables = insp.get_table_names()
print(f'Existing tables: {tables}')

# If this is a fresh DB (no tables at all), create everything and stamp
if 'users' not in tables:
    print('Fresh database — creating all tables...')
    from app.database import Base
    import app.models  # ensure all models are registered
    Base.metadata.create_all(engine)
    print('Tables created.')

# Ensure alembic_version table exists
if 'alembic_version' not in insp.get_table_names():
    print('Creating alembic_version table and stamping head...')
    with engine.begin() as conn:
        conn.execute(text('''CREATE TABLE IF NOT EXISTS alembic_version (
            version_num VARCHAR(32) NOT NULL,
            CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
        )'''))
"

echo "Running alembic migrations..."
alembic upgrade head
echo "Migrations complete."

echo "Starting uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1
