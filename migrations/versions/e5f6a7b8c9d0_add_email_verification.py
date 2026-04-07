"""add_email_verification

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-04-07 13:00:00.000000

"""
from __future__ import annotations
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add email_verified column to users (existing users already have emails — mark as verified)
    op.add_column('users', sa.Column('email_verified', sa.Boolean(), nullable=False,
                                     server_default='true'))

    op.create_table(
        'email_verification_codes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('code', sa.String(6), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('used', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_email_verification_codes_user_id', 'email_verification_codes', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_email_verification_codes_user_id', 'email_verification_codes')
    op.drop_table('email_verification_codes')
    op.drop_column('users', 'email_verified')
