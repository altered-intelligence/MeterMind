"""add import batches + bill dedup columns

Revision ID: c7e2a1f4d9b0
Revises: b58bddce981e
Create Date: 2026-06-16 09:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7e2a1f4d9b0'
down_revision: Union[str, None] = 'b58bddce981e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'import_batches',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('total_files', sa.Integer(), nullable=False),
        sa.Column('imported_count', sa.Integer(), nullable=False),
        sa.Column('duplicate_count', sa.Integer(), nullable=False),
        sa.Column('failed_count', sa.Integer(), nullable=False),
        sa.Column('warning_count', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('import_batches', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_import_batches_user_id'), ['user_id'], unique=False)

    op.create_table(
        'import_batch_items',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('import_batch_id', sa.Integer(), nullable=False),
        sa.Column('filename', sa.String(length=256), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('bill_id', sa.Integer(), nullable=True),
        sa.Column('error_code', sa.String(length=64), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('parser_confidence', sa.String(length=16), nullable=True),
        sa.Column('warning_count', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['import_batch_id'], ['import_batches.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['bill_id'], ['bills.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('import_batch_items', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_import_batch_items_import_batch_id'), ['import_batch_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_import_batch_items_bill_id'), ['bill_id'], unique=False
        )

    # New dedup/provenance columns on bills.
    #
    # IMPORTANT: use plain ``ALTER TABLE ... ADD COLUMN`` (op.add_column), NOT
    # batch_alter_table. On SQLite, batch mode RECREATES the table (create tmp, copy,
    # DROP old, rename). Dropping the old ``bills`` table cascades ON DELETE CASCADE to
    # every child (electric_services, gas_services, charge_line_items, ...), wiping their
    # rows even though the bills survive. Adding columns in place avoids the recreate.
    #
    # ``import_batch_id`` is added as a plain column (no FK constraint): SQLite cannot add
    # a foreign key to an existing table without recreating it, and the ORM enforces the
    # SET NULL semantics. Fresh databases (create_all / Postgres) still get the real FK
    # from the model definition.
    op.add_column('bills', sa.Column('pdf_sha256_hash', sa.String(length=64), nullable=True))
    op.add_column('bills', sa.Column('parsed_text_sha256_hash', sa.String(length=64), nullable=True))
    op.add_column('bills', sa.Column('source_filename', sa.String(length=256), nullable=True))
    op.add_column('bills', sa.Column('import_batch_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_bills_pdf_sha256_hash'), 'bills', ['pdf_sha256_hash'], unique=False)
    op.create_index(
        op.f('ix_bills_parsed_text_sha256_hash'), 'bills', ['parsed_text_sha256_hash'], unique=False
    )
    op.create_index(op.f('ix_bills_import_batch_id'), 'bills', ['import_batch_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_bills_import_batch_id'), table_name='bills')
    op.drop_index(op.f('ix_bills_parsed_text_sha256_hash'), table_name='bills')
    op.drop_index(op.f('ix_bills_pdf_sha256_hash'), table_name='bills')
    op.drop_column('bills', 'import_batch_id')
    op.drop_column('bills', 'source_filename')
    op.drop_column('bills', 'parsed_text_sha256_hash')
    op.drop_column('bills', 'pdf_sha256_hash')

    with op.batch_alter_table('import_batch_items', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_import_batch_items_bill_id'))
        batch_op.drop_index(batch_op.f('ix_import_batch_items_import_batch_id'))
    op.drop_table('import_batch_items')

    with op.batch_alter_table('import_batches', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_import_batches_user_id'))
    op.drop_table('import_batches')
