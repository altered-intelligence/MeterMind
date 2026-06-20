"""add security_audit_events

Revision ID: a1c5e8f2b9d4
Revises: f6b1d9e3a7c2
Create Date: 2026-06-19 10:19:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c5e8f2b9d4'
down_revision: Union[str, None] = 'f6b1d9e3a7c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'security_audit_events',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('event_type', sa.String(length=48), nullable=False),
        sa.Column('object_type', sa.String(length=32), nullable=True),
        sa.Column('object_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('metadata_json', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_security_audit_events_user_id'), 'security_audit_events', ['user_id'])
    op.create_index(op.f('ix_security_audit_events_event_type'), 'security_audit_events', ['event_type'])
    op.create_index(op.f('ix_security_audit_events_created_at'), 'security_audit_events', ['created_at'])


def downgrade() -> None:
    op.drop_index(op.f('ix_security_audit_events_created_at'), table_name='security_audit_events')
    op.drop_index(op.f('ix_security_audit_events_event_type'), table_name='security_audit_events')
    op.drop_index(op.f('ix_security_audit_events_user_id'), table_name='security_audit_events')
    op.drop_table('security_audit_events')
