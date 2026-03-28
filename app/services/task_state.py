"""Persistent task state — survives server restarts.

Saves batch import progress to a JSON file so that:
- In-progress batch states can be restored after a restart
- The browser can reconnect to see what happened
- Search sessions are recovered automatically from the DB
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from app.config import BASE_DIR

logger = logging.getLogger(__name__)

STATE_FILE = BASE_DIR / "data" / "task_state.json"
_lock = threading.Lock()


def _load() -> dict:
    if not STATE_FILE.exists():
        return {"batches": {}}
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"batches": {}}


def _save(data: dict):
    import shutil
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    # L7: shutil.move is safe on both POSIX (atomic) and Windows (copies then deletes)
    shutil.move(str(tmp), str(STATE_FILE))


def save_batch(batch_id: str, user_id: int, batch_state: dict) -> None:
    """Persist a batch's current state (called after every status change)."""
    with _lock:
        data = _load()
        data["batches"][batch_id] = {
            "user_id": user_id,
            "batch": batch_state,
            "saved_at": time.time(),
        }
        _save(data)


def delete_batch(batch_id: str) -> None:
    """Remove a fully-completed batch from persistent state."""
    with _lock:
        data = _load()
        data["batches"].pop(batch_id, None)
        _save(data)


def load_saved_batches() -> list[dict]:
    """Return all saved batches: [{"batch_id", "user_id", "batch"}, ...]"""
    with _lock:
        data = _load()
    return [
        {"batch_id": bid, "user_id": v["user_id"], "batch": v["batch"]}
        for bid, v in data["batches"].items()
    ]


def restore_on_startup() -> tuple[dict, dict]:
    """
    Called at app startup. Returns:
      (_batch_progress_patch, _user_batches_patch)

    Any batch that was marked running=True is flipped to interrupted so the
    browser can display what happened without re-running anything.
    """
    saved = load_saved_batches()
    batch_progress: dict[str, dict] = {}
    user_batches: dict[int, list[str]] = {}

    for entry in saved:
        bid = entry["batch_id"]
        uid = entry["user_id"]
        batch = entry["batch"]

        # Mark any job that was "importing" as interrupted
        for job in batch.get("jobs", []):
            if job.get("status") == "importing":
                job["status"] = "error"
                job["error"] = "Server restarted — import was interrupted"

        # If batch was still marked running, stop it
        if batch.get("running"):
            batch["running"] = False

        batch_progress[bid] = batch
        user_batches.setdefault(uid, []).append(bid)
        logger.info(f"Restored batch {bid} for user {uid} ({batch.get('done')}/{batch.get('total')} jobs)")

    return batch_progress, user_batches
