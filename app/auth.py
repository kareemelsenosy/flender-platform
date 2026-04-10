"""Authentication: password hashing, session middleware, login/logout."""
from __future__ import annotations

from functools import wraps

import bcrypt
from fastapi import Request
from fastapi.responses import RedirectResponse
from itsdangerous import URLSafeTimedSerializer

from app.config import SECRET_KEY

_signer = URLSafeTimedSerializer(SECRET_KEY)
SESSION_COOKIE = "flender_session"
SESSION_MAX_AGE = 86400 * 7  # 7 days


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_session_token(user_id: int) -> str:
    return _signer.dumps({"uid": user_id})


def decode_session_token(token: str) -> int | None:
    try:
        data = _signer.loads(token, max_age=SESSION_MAX_AGE)
        return data.get("uid")
    except Exception:
        return None


def get_current_user_id(request: Request) -> int | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    uid = decode_session_token(token)
    if not uid:
        return None
    return uid


def get_current_user_id_db(request: Request, db) -> int | None:
    """Variant that also verifies the user still exists in DB. (M7)
    Pass a SQLAlchemy Session to use this check."""
    uid = get_current_user_id(request)
    if not uid:
        return None
    from app.models import User
    user = db.query(User).filter(User.id == uid, User.is_active == True).first()
    return uid if user else None


def set_session_cookie(response, user_id: int, request: Request | None = None):
    token = create_session_token(user_id)
    secure = True if request is None else request.url.scheme == "https"
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )
    return response


def clear_session_cookie(response, request: Request | None = None):
    secure = True if request is None else request.url.scheme == "https"
    response.delete_cookie(SESSION_COOKIE, path="/", secure=secure, httponly=True, samesite="lax")
    return response


def login_required(func):
    """Decorator for route handlers that require authentication."""
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        user_id = get_current_user_id(request)
        if not user_id:
            return RedirectResponse("/login", status_code=302)
        request.state.user_id = user_id
        return await func(request, *args, **kwargs)
    return wrapper
