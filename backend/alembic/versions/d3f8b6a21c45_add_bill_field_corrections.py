"""add bill_field_corrections

Revision ID: d3f8b6a21c45
Revises: c7e2a1f4d9b0
Create Date: 2026-06-16 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3f8b6a21c45'
down_revision: Union[str, None] = 'c7e2a1f4d9b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'bill_field_corrections',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('bill_id', sa.Integer(), nullable=False),
        sa.Column('field_path', sa.String(length=128), nullable=False),
        sa.Column('original_value', sa.Text(), nullable=True),
        sa.Column('corrected_value', sa.Text(), nullable=True),
        sa.Column('corrected_value_type', sa.String(length=16), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('corrected_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['bill_id'], ['bills.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['corrected_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('bill_field_corrections', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_bill_field_corrections_bill_id'), ['bill_id'], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table('bill_field_corrections', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_bill_field_corrections_bill_id'))
    op.drop_table('bill_field_corrections')
