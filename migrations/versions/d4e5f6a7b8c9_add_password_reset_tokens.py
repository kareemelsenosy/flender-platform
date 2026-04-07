"""add_password_reset_tokens

Revision ID: d4e5f6a7b8c9
Revises: c4a2f01d8e3a
Create Date: 2026-04-07 12:00:00.000000

"""
from __future__ import annotations
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c4a2f01d8e3a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if 'password_reset_tokens' not in insp.get_table_names():
        op.create_table(
            'password_reset_tokens',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('token', sa.String(64), unique=True, nullable=False),
            sa.Column('expires_at', sa.DateTime(), nullable=False),
            sa.Column('used', sa.Boolean(), nullable=False, server_default='false'),
            sa.Column('created_at', sa.DateTime(), nullable=True),
        )
        op.create_index('ix_password_reset_tokens_token', 'password_reset_tokens', ['token'])


def downgrade() -> None:
    op.drop_index('ix_password_reset_tokens_token', 'password_reset_tokens')
    op.drop_table('password_reset_tokens')
