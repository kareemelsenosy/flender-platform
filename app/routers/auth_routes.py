"""Authentication routes: login, register, logout."""
from __future__ import annotations

import time
from collections import defaultdict

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.auth import (
    check_password, clear_session_cookie, get_current_user_id,
    hash_password, set_session_cookie,
)
from app.database import get_db
from app.main import templates
from app.models import User

router = APIRouter()

# M2: Simple in-memory rate limiter — {ip: [timestamp, ...]}
_login_attempts: dict[str, list[float]] = defaultdict(list)
_RATE_WINDOW = 300   # 5-minute window
_RATE_MAX    = 10    # max 10 attempts per window per IP


def _is_rate_limited(ip: str) -> bool:
    now = time.time()
    attempts = _login_attempts[ip]
    # Drop old attempts outside the window
    _login_attempts[ip] = [t for t in attempts if now - t < _RATE_WINDOW]
    if len(_login_attempts[ip]) >= _RATE_MAX:
        return True
    _login_attempts[ip].append(now)
    return False


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    uid = get_current_user_id(request)
    if uid:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login(request: Request, username: str = Form(default=""), password: str = Form(default=""),
                db: DBSession = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    if _is_rate_limited(ip):
        return templates.TemplateResponse(request, "login.html", {
            "error": "Too many login attempts. Please wait 5 minutes.",
        }, status_code=429)

    user = db.query(User).filter(User.username == username).first()
    if not user or not check_password(password, user.password_hash):
        return templates.TemplateResponse(request, "login.html", {
            "error": "Invalid username or password",
        })
    response = RedirectResponse("/", status_code=302)
    return set_session_cookie(response, user.id)


@router.post("/register")
async def register(request: Request, username: str = Form(default=""), password: str = Form(default=""),
                   email: str = Form(default=""), db: DBSession = Depends(get_db)):
    # Validate @flendergroup.com email
    email = email.strip().lower()
    if not email or not email.endswith("@flendergroup.com"):
        return templates.TemplateResponse(request, "login.html", {
            "error": "Registration is restricted to @flendergroup.com email addresses.", "show_register": True,
        })

    # M1: Server-side validation
    if len(username.strip()) < 3:
        return templates.TemplateResponse(request, "login.html", {
            "error": "Username must be at least 3 characters.", "show_register": True,
        })
    if len(password) < 8:
        return templates.TemplateResponse(request, "login.html", {
            "error": "Password must be at least 8 characters.", "show_register": True,
        })

    existing = db.query(User).filter(User.username == username.strip()).first()
    if existing:
        return templates.TemplateResponse(request, "login.html", {
            "error": "Username already taken.", "show_register": True,
        })
    existing_email = db.query(User).filter(User.email == email).first()
    if existing_email:
        return templates.TemplateResponse(request, "login.html", {
            "error": "This email is already registered.", "show_register": True,
        })

    user = User(username=username.strip(), email=email, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    response = RedirectResponse("/", status_code=302)
    return set_session_cookie(response, user.id)


@router.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=302)
    return clear_session_cookie(response)
