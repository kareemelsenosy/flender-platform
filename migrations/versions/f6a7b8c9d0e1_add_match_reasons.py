"""add match_reasons_json to unique_items and search_cache

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-05-01 12:00:00.000000

"""
from __future__ import annotations
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    for table in ("unique_items", "search_cache"):
        existing = [c["name"] for c in insp.get_columns(table)]
        if "match_reasons_json" not in existing:
            op.add_column(
                table,
                sa.Column("match_reasons_json", sa.Text(), nullable=True, server_default="{}"),
            )


def downgrade() -> None:
    for table in ("unique_items", "search_cache"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("match_reasons_json")
