#!/usr/bin/env bash
set -e
echo "=== FLENDER Platform — Railway Setup ==="
railway init --name flender-platform
echo "Adding PostgreSQL..."
railway add --database postgres
SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
railway variables set SECRET_KEY="$SECRET"
railway variables set UPLOAD_DIR="/app/uploads"
railway variables set OUTPUT_DIR="/app/output"
echo "Deploying..."
railway up --detach
echo "Waiting for deploy..."
sleep 20
echo "Running migrations..."
railway run alembic revision --autogenerate -m "initial"
railway run alembic upgrade head
echo "Done! Opening app..."
railway open
