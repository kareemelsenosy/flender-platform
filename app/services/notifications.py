"""Notification store — persists to disk so alerts survive server restarts. (M3)"""
from __future__ import annotations

import json
import threading
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path

from app.config import BASE_DIR

_NOTIF_FILE = BASE_DIR / "data" / "notifications.json"
_lock = threading.Lock()

# user_id (as int key) -> deque of notification dicts (capped at 100)
_store: dict[int, deque] = defaultdict(lambda: deque(maxlen=100))


# ── Persistence helpers ───────────────────────────────────────────────────────

def _load_from_disk() -> None:
    """Load saved notifications from disk into _store."""
    if not _NOTIF_FILE.exists():
        return
    try:
        with open(_NOTIF_FILE) as f:
            data = json.load(f)
        for uid_str, notifs in data.items():
            uid = int(uid_str)
            for n in notifs:
                _store[uid].append(n)
    except Exception:
        pass


def _save_to_disk() -> None:
    """Persist current _store to disk (best-effort atomic write)."""
    try:
        _NOTIF_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            str(uid): list(dq)
            for uid, dq in _store.items()
        }
        tmp = _NOTIF_FILE.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(payload, f)
        tmp.replace(_NOTIF_FILE)
    except Exception:
        pass


# Load existing notifications at import time
_load_from_disk()


# ── Public API ────────────────────────────────────────────────────────────────

def add_notification(
    user_id: int,
    notif_type: str,
    title: str,
    message: str,
    session_id: int | None = None,
    actions: list[dict] | None = None,
) -> None:
    """Add a notification for a user and persist to disk."""
    notif = {
        "id": str(uuid.uuid4()),
        "type": notif_type,
        "title": title,
        "message": message,
        "session_id": session_id,
        "actions": actions or [],
        "ts": time.time(),
        "seen": False,
    }
    with _lock:
        _store[user_id].append(notif)
        _save_to_disk()


def poll_notifications(user_id: int) -> list[dict]:
    """Return all unseen notifications and mark them seen."""
    with _lock:
        notifs = _store.get(user_id)
        if not notifs:
            return []
        unseen = [n for n in notifs if not n["seen"]]
        for n in unseen:
            n["seen"] = True
        if unseen:
            _save_to_disk()
        return unseen
