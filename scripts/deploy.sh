#!/usr/bin/env bash
# Usage: ./scripts/deploy.sh <RAILWAY_TOKEN>
# Get token: https://railway.app → Account Settings → Tokens → New Token
set -e

TOKEN="${1:-$RAILWAY_TOKEN}"
if [ -z "$TOKEN" ]; then
  echo "Usage: ./scripts/deploy.sh <RAILWAY_TOKEN>"
  echo "Get token at: https://railway.app → Account Settings → Tokens"
  exit 1
fi

export RAILWAY_TOKEN="$TOKEN"
cd "$(dirname "$0")/.."

echo "=== FLENDER Platform Deploy ==="

# Init project linked to GitHub repo
echo "▶ Creating Railway project..."
railway init --name flender-platform 2>/dev/null || true

# Add PostgreSQL
echo "▶ Adding PostgreSQL database..."
railway add --database postgres 2>/dev/null || echo "  (postgres may already exist)"

# Generate secrets and set all env vars
SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
echo "▶ Setting environment variables..."
railway variables set \
  SECRET_KEY="$SECRET" \
  UPLOAD_DIR="/app/uploads" \
  OUTPUT_DIR="/app/output"

# Also set token as GitHub Actions secret for future auto-deploys
if command -v gh &>/dev/null; then
  echo "▶ Saving RAILWAY_TOKEN to GitHub Actions secrets..."
  echo "$TOKEN" | gh secret set RAILWAY_TOKEN --repo kareemelsenosy/flender-platform
fi

# Deploy
echo "▶ Deploying..."
railway up --detach

echo "▶ Waiting for deploy to start (30s)..."
sleep 30

# Migrations
echo "▶ Running database migrations..."
railway run alembic revision --autogenerate -m "initial" 2>/dev/null || true
railway run alembic upgrade head

# Create volumes reminder
echo ""
echo "✓ Deployed!"
echo ""
echo "Add persistent volumes in Railway dashboard:"
echo "  → your service → Volumes → Add Volume"
echo "  Mount paths: /app/uploads  /app/output  /app/data"
echo ""

# Open
railway open
