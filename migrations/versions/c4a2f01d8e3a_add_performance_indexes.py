"""add_performance_indexes

Revision ID: c4a2f01d8e3a
Revises: b3b18b01e99f
Create Date: 2026-03-29 14:30:00.000000

"""
from __future__ import annotations
from typing import Sequence, Union
from alembic import op


revision: str = 'c4a2f01d8e3a'
down_revision: Union[str, None] = 'b3b18b01e99f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index('ix_sessions_user_id', 'sessions', ['user_id'])
    op.create_index('ix_sessions_status', 'sessions', ['status'])
    op.create_index('ix_unique_items_session_id', 'unique_items', ['session_id'])
    op.create_index('ix_unique_items_session_search', 'unique_items', ['session_id', 'search_status'])
    op.create_index('ix_unique_items_session_review', 'unique_items', ['session_id', 'review_status'])
    op.create_index('ix_generated_files_session_id', 'generated_files', ['session_id'])


def downgrade() -> None:
    op.drop_index('ix_generated_files_session_id', 'generated_files')
    op.drop_index('ix_unique_items_session_review', 'unique_items')
    op.drop_index('ix_unique_items_session_search', 'unique_items')
    op.drop_index('ix_unique_items_session_id', 'unique_items')
    op.drop_index('ix_sessions_status', 'sessions')
    op.drop_index('ix_sessions_user_id', 'sessions')
