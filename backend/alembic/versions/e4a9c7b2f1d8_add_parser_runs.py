"""add parser runs + bill parser-version provenance columns

Revision ID: e4a9c7b2f1d8
Revises: d3f8b6a21c45
Create Date: 2026-06-16 09:40:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e4a9c7b2f1d8'
down_revision: Union[str, None] = 'd3f8b6a21c45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CURRENT_SCHEMA_VERSION = "coserv-bill-schema-v1"
_FALLBACK_PARSER_VERSION = "coserv-v0.1.0"


def upgrade() -> None:
    # New provenance columns on bills.
    #
    # IMPORTANT: plain ``ALTER TABLE ... ADD COLUMN`` (op.add_column), NOT batch_alter_table.
    # On SQLite, batch mode RECREATES the table (create tmp, copy, DROP old, rename); the DROP
    # cascades ON DELETE CASCADE to every child (electric_services, line_items, ...), wiping
    # their rows. Adding columns in place avoids the recreate.
    op.add_column('bills', sa.Column('parser_schema_version', sa.String(length=48), nullable=True))
    op.add_column('bills', sa.Column('parsed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('bills', sa.Column('parse_duration_ms', sa.Integer(), nullable=True))

    # New table — created fresh, so a real FK to bills is fine on SQLite.
    op.create_table(
        'parser_runs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('bill_id', sa.Integer(), nullable=False),
        sa.Column('provider_name', sa.String(length=64), nullable=False),
        sa.Column('parser_version', sa.String(length=32), nullable=True),
        sa.Column('parser_schema_version', sa.String(length=48), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('warning_count', sa.Integer(), nullable=False),
        sa.Column('error_code', sa.String(length=64), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('parse_duration_ms', sa.Integer(), nullable=True),
        sa.Column('trigger', sa.String(length=16), nullable=True),
        sa.ForeignKeyConstraint(['bill_id'], ['bills.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_parser_runs_bill_id'), 'parser_runs', ['bill_id'], unique=False)

    # Backfill provenance for already-ingested bills: schema = current, parsed_at = created_at.
    op.execute(
        sa.text(
            "UPDATE bills SET parser_schema_version = :schema, parsed_at = created_at "
            "WHERE parser_schema_version IS NULL"
        ).bindparams(schema=_CURRENT_SCHEMA_VERSION)
    )

    # Backfill one synthetic 'completed' run per existing bill so every parse is auditable.
    op.execute(
        sa.text(
            "INSERT INTO parser_runs "
            "(bill_id, provider_name, parser_version, parser_schema_version, started_at, "
            " completed_at, status, warning_count, parse_duration_ms, trigger) "
            "SELECT id, COALESCE(provider_name, 'CoServ'), "
            "       COALESCE(parser_version, :pver), :schema, created_at, created_at, "
            "       'completed', 0, NULL, 'backfill' "
            "FROM bills"
        ).bindparams(pver=_FALLBACK_PARSER_VERSION, schema=_CURRENT_SCHEMA_VERSION)
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_parser_runs_bill_id'), table_name='parser_runs')
    op.drop_table('parser_runs')
    op.drop_column('bills', 'parse_duration_ms')
    op.drop_column('bills', 'parsed_at')
    op.drop_column('bills', 'parser_schema_version')
