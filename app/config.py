"""Application configuration via environment variables."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'flender.db'}")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-in-production")

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(BASE_DIR / "uploads")))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(BASE_DIR / "output")))

# How long a generated Order Sheet (Excel / images ZIP) stays downloadable
# before the disk-cleanup sweeper may remove it and its images folder. The old
# 24-hour window was too short — users returned the next day to find their
# result gone and had to regenerate the whole sheet. Kept configurable so the
# window can be dialed back from the server if the output volume gets tight.
GENERATED_FILE_RETENTION_DAYS = int(os.getenv("GENERATED_FILE_RETENTION_DAYS", "30"))

CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GOOGLE_SEARCH_KEY = os.getenv("GOOGLE_SEARCH_KEY", "")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID", "")

# Email / SMTP config for password reset
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)
APP_BASE_URL = os.getenv("APP_BASE_URL", "https://ordersheet.flendergroup.com")
EMAIL_VERIFICATION_REQUIRED = _env_bool(
    "EMAIL_VERIFICATION_REQUIRED",
    bool(SMTP_USER and SMTP_PASSWORD),
)
INTERNAL_API_ENABLED = _env_bool("INTERNAL_API_ENABLED", False)

# ── Operations OS: supplier email intake ─────────────────────────────────────
# n8n (or any mail workflow) posts the forwarded supplier email here. The
# endpoint stays disabled — a plain 404 — until a key is set, so it can never
# be left open by accident on a deploy that has not configured it.
INTAKE_API_KEY = os.getenv("INTAKE_API_KEY", "")
# Which account owns collections created by the mailbox. Falls back to the
# first active user so a fresh install still works in testing.
INTAKE_OWNER_EMAIL = os.getenv("INTAKE_OWNER_EMAIL", "")
# Per-attachment cap for intake. Image packages are the big ones; a season
# drop of packshots is routinely several hundred megabytes.
INTAKE_MAX_FILE_MB = int(os.getenv("INTAKE_MAX_FILE_MB", "500"))

# URL of the Social Media Tracker tool — shown as a tile on the AI Tools hub.
# Local dev default; in production set SMT_URL=https://smt.flendergroup.com
SMT_URL = os.getenv("SMT_URL", "http://localhost:3000")

# Ensure dirs exist
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
