"""Authentication routes: login, register, logout, forgot/reset password."""
from __future__ import annotations

import os
import secrets
import smtplib
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.auth import (
    check_password, clear_session_cookie, get_current_user_id,
    hash_password, set_session_cookie,
)
from app.config import (
    APP_BASE_URL, SMTP_FROM, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_USER,
)
from app.database import get_db
from app.main import templates
from app.models import PasswordResetToken, User

router = APIRouter()

# Simple in-memory rate limiter — {ip: [timestamp, ...]}
_login_attempts: dict[str, list[float]] = defaultdict(list)
_RATE_WINDOW = 300   # 5-minute window
_RATE_MAX    = 10    # max 10 attempts per window per IP


def _is_rate_limited(ip: str) -> bool:
    now = time.time()
    _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < _RATE_WINDOW]
    if len(_login_attempts[ip]) >= _RATE_MAX:
        return True
    _login_attempts[ip].append(now)
    return False


def _send_reset_email(to_email: str, reset_url: str) -> bool:
    """Send password reset email. Returns True on success."""
    if not SMTP_USER or not SMTP_PASSWORD:
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "FLENDER — Reset Your Password"
        msg["From"] = SMTP_FROM or SMTP_USER
        msg["To"] = to_email

        text = f"Click the link to reset your password:\n{reset_url}\n\nThis link expires in 1 hour."
        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;padding:32px 24px">
          <h2 style="font-family:'Arial Narrow',Arial,sans-serif;letter-spacing:4px;font-size:1.6rem;margin:0 0 8px">FLENDER</h2>
          <p style="color:#6B7280;margin:0 0 24px">Order Sheet Organizer</p>
          <hr style="border:none;border-top:1px solid #E5E7EB;margin-bottom:24px">
          <h3 style="margin:0 0 12px;color:#111827">Reset Your Password</h3>
          <p style="color:#374151;margin:0 0 24px">Click the button below to reset your password. This link expires in <strong>1 hour</strong>.</p>
          <a href="{reset_url}" style="display:inline-block;background:#111827;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:600;font-size:15px">Reset Password</a>
          <p style="color:#9CA3AF;font-size:12px;margin:24px 0 0">If you didn't request this, ignore this email. Your password won't change.</p>
        </div>
        """
        msg.attach(MIMEText(text, "plain"))
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM or SMTP_USER, to_email, msg.as_string())
        return True
    except Exception:
        return False


# ─── Login ────────────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    uid = get_current_user_id(request)
    if uid:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login(request: Request,
                username: str = Form(default=""),
                password: str = Form(default=""),
                db: DBSession = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    if _is_rate_limited(ip):
        return templates.TemplateResponse(request, "login.html", {
            "error": "Too many login attempts. Please wait 5 minutes.",
        }, status_code=429)

    identifier = username.strip()
    # Accept email or username
    user = (
        db.query(User).filter(User.email == identifier).first()
        or db.query(User).filter(User.username == identifier).first()
    )
    if not user or not check_password(password, user.password_hash):
        return templates.TemplateResponse(request, "login.html", {
            "error": "Invalid username/email or password",
        })
    response = RedirectResponse("/", status_code=302)
    return set_session_cookie(response, user.id)


# ─── Register ─────────────────────────────────────────────────────────────────

@router.post("/register")
async def register(request: Request,
                   username: str = Form(default=""),
                   password: str = Form(default=""),
                   email: str = Form(default=""),
                   db: DBSession = Depends(get_db)):
    email = email.strip().lower()
    if not email or not email.endswith("@flendergroup.com"):
        return templates.TemplateResponse(request, "login.html", {
            "error": "Registration is restricted to @flendergroup.com email addresses.",
            "show_register": True,
        })
    if len(username.strip()) < 3:
        return templates.TemplateResponse(request, "login.html", {
            "error": "Username must be at least 3 characters.", "show_register": True,
        })
    if len(password) < 8:
        return templates.TemplateResponse(request, "login.html", {
            "error": "Password must be at least 8 characters.", "show_register": True,
        })

    if db.query(User).filter(User.username == username.strip()).first():
        return templates.TemplateResponse(request, "login.html", {
            "error": "Username already taken.", "show_register": True,
        })
    if db.query(User).filter(User.email == email).first():
        return templates.TemplateResponse(request, "login.html", {
            "error": "This email is already registered.", "show_register": True,
        })

    user = User(username=username.strip(), email=email, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    response = RedirectResponse("/", status_code=302)
    return set_session_cookie(response, user.id)


# ─── Forgot password ──────────────────────────────────────────────────────────

@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    return templates.TemplateResponse(request, "forgot_password.html", {"sent": False, "error": None})


@router.post("/forgot-password")
async def forgot_password(request: Request,
                          email: str = Form(default=""),
                          db: DBSession = Depends(get_db)):
    email = email.strip().lower()
    user = db.query(User).filter(User.email == email).first()

    # Always show success to prevent email enumeration
    if user and user.email:
        # Invalidate old tokens
        db.query(PasswordResetToken).filter(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used == False,
        ).delete()

        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        db.add(PasswordResetToken(user_id=user.id, token=token, expires_at=expires))
        db.commit()

        reset_url = f"{APP_BASE_URL}/reset-password/{token}"
        _send_reset_email(user.email, reset_url)

    return templates.TemplateResponse(request, "forgot_password.html", {"sent": True, "error": None})


# ─── Reset password ───────────────────────────────────────────────────────────

@router.get("/reset-password/{token}", response_class=HTMLResponse)
async def reset_password_page(token: str, request: Request, db: DBSession = Depends(get_db)):
    record = db.query(PasswordResetToken).filter(
        PasswordResetToken.token == token,
        PasswordResetToken.used == False,
    ).first()

    expires = record.expires_at if record else None
    if expires and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)

    valid = record and expires and expires > datetime.now(timezone.utc)
    return templates.TemplateResponse(request, "reset_password.html", {
        "token": token, "valid": valid, "error": None, "success": False,
    })


@router.post("/reset-password/{token}")
async def reset_password(token: str, request: Request,
                         password: str = Form(default=""),
                         db: DBSession = Depends(get_db)):
    record = db.query(PasswordResetToken).filter(
        PasswordResetToken.token == token,
        PasswordResetToken.used == False,
    ).first()

    expires = record.expires_at if record else None
    if expires and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)

    if not record or not expires or expires <= datetime.now(timezone.utc):
        return templates.TemplateResponse(request, "reset_password.html", {
            "token": token, "valid": False, "error": "Link expired or invalid.", "success": False,
        })

    if len(password) < 8:
        return templates.TemplateResponse(request, "reset_password.html", {
            "token": token, "valid": True,
            "error": "Password must be at least 8 characters.", "success": False,
        })

    user = db.query(User).get(record.user_id)
    if user:
        user.password_hash = hash_password(password)
    record.used = True
    db.commit()

    return templates.TemplateResponse(request, "reset_password.html", {
        "token": token, "valid": True, "error": None, "success": True,
    })


# ─── Logout ───────────────────────────────────────────────────────────────────

@router.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=302)
    return clear_session_cookie(response)
