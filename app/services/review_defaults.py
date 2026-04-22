"""Helpers for default review approval behavior."""
from __future__ import annotations

from sqlalchemy import or_, update
from sqlalchemy.orm import Session as DBSession

from app.models import UniqueItem


def materialize_default_review_approvals(db: DBSession, session_id: int) -> int:
    """Approve untouched searched items by default.

    Once search has chosen a `suggested_url`, we treat that as the approved
    image unless the user later edits or skips it. This also repairs older
    sessions that were left in a pending state despite having a chosen image.
    """
    fixed_existing = db.execute(
        update(UniqueItem)
        .where(
            UniqueItem.session_id == session_id,
            UniqueItem.search_status == "done",
            UniqueItem.review_status == "pending",
            UniqueItem.approved_url.isnot(None),
            UniqueItem.approved_url != "",
        )
        .values(
            review_status="approved",
            auto_selected=True,
        )
    )

    fixed_suggested = db.execute(
        update(UniqueItem)
        .where(
            UniqueItem.session_id == session_id,
            UniqueItem.search_status == "done",
            UniqueItem.review_status == "pending",
            or_(UniqueItem.approved_url.is_(None), UniqueItem.approved_url == ""),
            UniqueItem.suggested_url.isnot(None),
            UniqueItem.suggested_url != "",
        )
        .values(
            approved_url=UniqueItem.suggested_url,
            review_status="approved",
            auto_selected=True,
        )
    )

    fixed = int(fixed_existing.rowcount or 0) + int(fixed_suggested.rowcount or 0)
    if fixed:
        db.commit()
    return fixed
