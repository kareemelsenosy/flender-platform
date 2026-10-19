"""Reading the product pictures a brand sends as a link.

HUF sends a Dropbox folder: 1.2 GB as one ZIP, most of it two catalogue PDFs,
the rest product flats named like ``KNOX-NYLON-JACKET_TAN_JK00532_TAN_01.jpg``.
Dropbox builds that ZIP on the fly, so there is no reading just its index:
it is downloaded once, in the background, the product photos are kept in the
collection's folder, and the ZIP is thrown away.

The image package then matches photos to products by name. Order sheets write
the item code with a dash (``JK00532-TAN``) where the photos use an underscore
(``JK00532_TAN``), so both are treated as the same.
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app.config import OUTPUT_DIR

logger = logging.getLogger(__name__)

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}


def photos_dir(job_id: int) -> Path:
    return OUTPUT_DIR / f"collection_{job_id}" / "photos"


def read_links_in_background(job_id: int) -> None:
    threading.Thread(target=_read_links, args=(job_id,), daemon=True).start()


def _save(job_id: int, **state) -> None:
    from app.database import SessionLocal
    from app.models import CollectionJob
    db = SessionLocal()
    try:
        job = db.get(CollectionJob, job_id)
        if job is not None:
            job.picture_index = {**job.picture_index, **state}
            db.commit()
    finally:
        db.close()


def _read_links(job_id: int) -> None:
    """Download every picture link and keep the photos. Never raises."""
    from app.core.image_sources import SourceFetchError, fetch_image_source
    from app.database import SessionLocal
    from app.models import CollectionJob

    db = SessionLocal()
    try:
        job = db.get(CollectionJob, job_id)
        links = list(job.picture_links) if job else []
    finally:
        db.close()
    if not links:
        _save(job_id, status="empty", files=[], error="")
        return

    _save(job_id, status="reading", error="",
          at=datetime.now(timezone.utc).isoformat())
    dest = photos_dir(job_id)
    work = dest.parent / "photo_download"
    shutil.rmtree(work, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    try:
        for link in links:
            try:
                got = fetch_image_source(link, work)
            except SourceFetchError as exc:
                errors.append(f"{link[:60]}…: {exc}")
                continue
            for name in got.get("files", []):
                _keep_photos(work / name, dest)
    except Exception as exc:                       # pragma: no cover — defensive
        logger.exception("picture links for collection %s", job_id)
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    files = sorted(p.name for p in dest.iterdir() if p.is_file())
    _save(job_id, status="error" if errors and not files else "done",
          files=files, error=" | ".join(errors)[:1000],
          at=datetime.now(timezone.utc).isoformat())


def _keep_photos(path: Path, dest: Path) -> None:
    """Move photos into ``dest``; from a ZIP, extract only the photos."""
    if path.suffix.lower() in PHOTO_EXTS:
        shutil.move(str(path), dest / path.name)
        return
    if path.suffix.lower() != ".zip" and not zipfile.is_zipfile(path):
        return
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            name = os.path.basename(info.filename)
            if info.is_dir() or not name or name.startswith("."):
                continue
            if os.path.splitext(name)[1].lower() not in PHOTO_EXTS:
                continue
            with zf.open(info) as src, open(dest / name, "wb") as out:
                shutil.copyfileobj(src, out)
    path.unlink(missing_ok=True)
