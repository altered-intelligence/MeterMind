"""add weather locations, daily observations, and bill weather summaries

Revision ID: f6b1d9e3a7c2
Revises: e4a9c7b2f1d8
Create Date: 2026-06-18 15:24:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6b1d9e3a7c2'
down_revision: Union[str, None] = 'e4a9c7b2f1d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # All three are NEW tables, so create_table with real FKs is safe on SQLite (no recreate
    # of an existing table -> no cascade-delete risk).
    op.create_table(
        'weather_locations',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('label', sa.String(length=128), nullable=True),
        sa.Column('latitude', sa.Float(), nullable=False),
        sa.Column('longitude', sa.Float(), nullable=False),
        sa.Column('timezone', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_weather_locations_user_id'), 'weather_locations', ['user_id'])

    op.create_table(
        'weather_daily_observations',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('weather_location_id', sa.Integer(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('avg_temp_f', sa.Float(), nullable=True),
        sa.Column('min_temp_f', sa.Float(), nullable=True),
        sa.Column('max_temp_f', sa.Float(), nullable=True),
        sa.Column('avg_humidity', sa.Float(), nullable=True),
        sa.Column('precipitation_inches', sa.Float(), nullable=True),
        sa.Column('heating_degree_days_65', sa.Float(), nullable=True),
        sa.Column('cooling_degree_days_65', sa.Float(), nullable=True),
        sa.Column('source', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['weather_location_id'], ['weather_locations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('weather_location_id', 'date', name='uq_weather_obs_loc_date'),
    )
    op.create_index(
        op.f('ix_weather_daily_observations_weather_location_id'),
        'weather_daily_observations', ['weather_location_id'],
    )
    op.create_index(
        op.f('ix_weather_daily_observations_date'), 'weather_daily_observations', ['date']
    )

    op.create_table(
        'bill_weather_summaries',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('bill_id', sa.Integer(), nullable=False),
        sa.Column('weather_location_id', sa.Integer(), nullable=False),
        sa.Column('service_start_date', sa.Date(), nullable=True),
        sa.Column('service_end_date', sa.Date(), nullable=True),
        sa.Column('days_observed', sa.Integer(), nullable=False),
        sa.Column('avg_temp_f', sa.Float(), nullable=True),
        sa.Column('avg_min_temp_f', sa.Float(), nullable=True),
        sa.Column('avg_max_temp_f', sa.Float(), nullable=True),
        sa.Column('total_precipitation_inches', sa.Float(), nullable=True),
        sa.Column('total_heating_degree_days_65', sa.Float(), nullable=True),
        sa.Column('total_cooling_degree_days_65', sa.Float(), nullable=True),
        sa.Column('days_over_95f', sa.Integer(), nullable=False),
        sa.Column('days_over_100f', sa.Integer(), nullable=False),
        sa.Column('days_below_32f', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['bill_id'], ['bills.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['weather_location_id'], ['weather_locations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('bill_id', 'weather_location_id', name='uq_bill_weather_loc'),
    )
    op.create_index(op.f('ix_bill_weather_summaries_bill_id'), 'bill_weather_summaries', ['bill_id'])
    op.create_index(
        op.f('ix_bill_weather_summaries_weather_location_id'),
        'bill_weather_summaries', ['weather_location_id'],
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_bill_weather_summaries_weather_location_id'), table_name='bill_weather_summaries')
    op.drop_index(op.f('ix_bill_weather_summaries_bill_id'), table_name='bill_weather_summaries')
    op.drop_table('bill_weather_summaries')
    op.drop_index(op.f('ix_weather_daily_observations_date'), table_name='weather_daily_observations')
    op.drop_index(op.f('ix_weather_daily_observations_weather_location_id'), table_name='weather_daily_observations')
    op.drop_table('weather_daily_observations')
    op.drop_index(op.f('ix_weather_locations_user_id'), table_name='weather_locations')
    op.drop_table('weather_locations')
