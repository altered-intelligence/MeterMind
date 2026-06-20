"""ORM models — normalized relational schema for the CoServ bill tracker.

Design goals:
  * **Migration-friendly / PostgreSQL-ready.** Proper typed columns (Date, Numeric,
    Boolean, ...), explicit foreign keys with ON DELETE behavior, and a custom
    ``Money`` type that stores exact ``Decimal`` values (as TEXT on SQLite to avoid
    float rounding, as ``NUMERIC`` on PostgreSQL). No SQLite-only features. Tables map
    cleanly to an Alembic autogenerate.
  * **Privacy.** Full account/meter numbers are never stored — only an HMAC hash, the
    last 4 chars, and a redacted address.

Hierarchy:
    User 1─* UtilityAccount
    User 1─* Bill *─1 UtilityAccount (nullable)
    Bill 1─1 ElectricService
    Bill 1─1 GasService
    Bill 1─* ChargeLineItem
    Bill 1─* BillSnapshot
    Bill 1─* ParserWarning
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    types,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class Money(types.TypeDecorator):
    """Exact decimal storage. TEXT on SQLite (no float rounding); NUMERIC on Postgres."""

    impl = Numeric
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(48))
        return dialect.type_descriptor(Numeric(18, 6))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        dec = value if isinstance(value, Decimal) else Decimal(str(value))
        return str(dec) if dialect.name == "sqlite" else dec

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value if isinstance(value, Decimal) else Decimal(str(value))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    utility_accounts: Mapped[List["UtilityAccount"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    bills: Mapped[List["Bill"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UtilityAccount(Base):
    __tablename__ = "utility_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "provider_name", "account_number_hash", name="uq_account"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_name: Mapped[str] = mapped_column(String(64), nullable=False)
    # NOT NULL: a utility_account is only ever created when an account number was parsed
    # (so a hash always exists); this makes the dedupe unique constraint meaningful.
    account_number_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    account_number_last4: Mapped[Optional[str]] = mapped_column(String(8))
    service_address_redacted: Mapped[Optional[str]] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user: Mapped["User"] = relationship(back_populates="utility_accounts")
    bills: Mapped[List["Bill"]] = relationship(back_populates="utility_account")


class Bill(Base):
    __tablename__ = "bills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    utility_account_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("utility_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider_name: Mapped[str] = mapped_column(String(64), nullable=False)

    bill_date: Mapped[Optional[date]] = mapped_column(Date, index=True)
    due_date: Mapped[Optional[date]] = mapped_column(Date)
    billing_period_start: Mapped[Optional[date]] = mapped_column(Date)
    billing_period_end: Mapped[Optional[date]] = mapped_column(Date)
    billing_days: Mapped[Optional[int]] = mapped_column(Integer)

    previous_balance: Mapped[Optional[Decimal]] = mapped_column(Money)
    payment_received: Mapped[Optional[Decimal]] = mapped_column(Money)
    balance_forward: Mapped[Optional[Decimal]] = mapped_column(Money)
    electric_service_total: Mapped[Optional[Decimal]] = mapped_column(Money)
    gas_service_total: Mapped[Optional[Decimal]] = mapped_column(Money)
    current_charges_total: Mapped[Optional[Decimal]] = mapped_column(Money)
    total_amount_due: Mapped[Optional[Decimal]] = mapped_column(Money)
    amount_due_after_due_date: Mapped[Optional[Decimal]] = mapped_column(Money)
    autopay_status: Mapped[Optional[str]] = mapped_column(String(32))

    pdf_storage_path: Mapped[Optional[str]] = mapped_column(String(512))
    parser_version: Mapped[Optional[str]] = mapped_column(String(32))
    # Output-schema contract version (separate from parser_version, which tracks extraction
    # logic). Lets a CoServ layout change be detected/reprocessed. Set on ingest + reprocess.
    parser_schema_version: Mapped[Optional[str]] = mapped_column(String(48))
    parser_confidence: Mapped[Optional[str]] = mapped_column(String(16))
    # When the *current* parsed data was produced, and how long that parse took. Updated on
    # every (re)process so the latest run's provenance lives on the bill; the full history is
    # in ``parser_runs``.
    parsed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    parse_duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    # Deduplication + provenance. Hashes let us reject byte-identical re-uploads (pdf hash)
    # and re-extracted-but-identical content (text hash); source_filename + import_batch_id
    # record where a bill came from during a bulk import.
    pdf_sha256_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    parsed_text_sha256_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    source_filename: Mapped[Optional[str]] = mapped_column(String(256))
    import_batch_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("import_batches.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user: Mapped["User"] = relationship(back_populates="bills")
    import_batch: Mapped[Optional["ImportBatch"]] = relationship(back_populates="bills")
    utility_account: Mapped[Optional["UtilityAccount"]] = relationship(back_populates="bills")
    electric_service: Mapped[Optional["ElectricService"]] = relationship(
        back_populates="bill", uselist=False, cascade="all, delete-orphan"
    )
    gas_service: Mapped[Optional["GasService"]] = relationship(
        back_populates="bill", uselist=False, cascade="all, delete-orphan"
    )
    line_items: Mapped[List["ChargeLineItem"]] = relationship(
        back_populates="bill", cascade="all, delete-orphan"
    )
    snapshots: Mapped[List["BillSnapshot"]] = relationship(
        back_populates="bill", cascade="all, delete-orphan"
    )
    warnings: Mapped[List["ParserWarning"]] = relationship(
        back_populates="bill", cascade="all, delete-orphan"
    )
    audit: Mapped[Optional["BillAudit"]] = relationship(
        back_populates="bill", uselist=False, cascade="all, delete-orphan"
    )
    corrections: Mapped[List["BillFieldCorrection"]] = relationship(
        back_populates="bill", cascade="all, delete-orphan"
    )
    parser_runs: Mapped[List["ParserRun"]] = relationship(
        back_populates="bill", cascade="all, delete-orphan", order_by="ParserRun.id"
    )
    weather_summaries: Mapped[List["BillWeatherSummary"]] = relationship(
        back_populates="bill", cascade="all, delete-orphan"
    )


class ElectricService(Base):
    __tablename__ = "electric_services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bill_id: Mapped[int] = mapped_column(
        ForeignKey("bills.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    service_rate: Mapped[Optional[str]] = mapped_column(String(128))
    meter_number_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    meter_number_last4: Mapped[Optional[str]] = mapped_column(String(8))
    service_start_date: Mapped[Optional[date]] = mapped_column(Date)
    service_end_date: Mapped[Optional[date]] = mapped_column(Date)
    billing_days: Mapped[Optional[int]] = mapped_column(Integer)
    last_read: Mapped[Optional[int]] = mapped_column(Integer)
    current_read: Mapped[Optional[int]] = mapped_column(Integer)
    multiplier: Mapped[Optional[Decimal]] = mapped_column(Money)
    kwh_used: Mapped[Optional[Decimal]] = mapped_column(Money)
    total_current_charges: Mapped[Optional[Decimal]] = mapped_column(Money)
    average_kwh_per_day: Mapped[Optional[Decimal]] = mapped_column(Money)
    effective_rate_per_kwh: Mapped[Optional[Decimal]] = mapped_column(Money)
    average_cost_per_day: Mapped[Optional[Decimal]] = mapped_column(Money)

    bill: Mapped["Bill"] = relationship(back_populates="electric_service")


class GasService(Base):
    __tablename__ = "gas_services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bill_id: Mapped[int] = mapped_column(
        ForeignKey("bills.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    service_rate: Mapped[Optional[str]] = mapped_column(String(128))
    meter_number_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    meter_number_last4: Mapped[Optional[str]] = mapped_column(String(8))
    service_start_date: Mapped[Optional[date]] = mapped_column(Date)
    service_end_date: Mapped[Optional[date]] = mapped_column(Date)
    billing_days: Mapped[Optional[int]] = mapped_column(Integer)
    last_read: Mapped[Optional[int]] = mapped_column(Integer)
    current_read: Mapped[Optional[int]] = mapped_column(Integer)
    multiplier: Mapped[Optional[Decimal]] = mapped_column(Money)
    ccf_used: Mapped[Optional[Decimal]] = mapped_column(Money)
    total_current_charges: Mapped[Optional[Decimal]] = mapped_column(Money)
    average_ccf_per_day: Mapped[Optional[Decimal]] = mapped_column(Money)
    effective_rate_per_ccf: Mapped[Optional[Decimal]] = mapped_column(Money)
    average_cost_per_day: Mapped[Optional[Decimal]] = mapped_column(Money)

    bill: Mapped["Bill"] = relationship(back_populates="gas_service")


class ChargeLineItem(Base):
    __tablename__ = "charge_line_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bill_id: Mapped[int] = mapped_column(
        ForeignKey("bills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    service_type: Mapped[str] = mapped_column(String(16), nullable=False)  # 'electric'|'gas'
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    quantity: Mapped[Optional[Decimal]] = mapped_column(Money)
    unit: Mapped[Optional[str]] = mapped_column(String(16))
    rate: Mapped[Optional[Decimal]] = mapped_column(Money)
    amount: Mapped[Optional[Decimal]] = mapped_column(Money)
    is_credit: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_text: Mapped[Optional[str]] = mapped_column(Text)

    bill: Mapped["Bill"] = relationship(back_populates="line_items")


class BillSnapshot(Base):
    __tablename__ = "bill_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bill_id: Mapped[int] = mapped_column(
        ForeignKey("bills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    service_type: Mapped[str] = mapped_column(String(16), nullable=False)  # 'electric'|'gas'
    average_cost_per_day_this_month: Mapped[Optional[Decimal]] = mapped_column(Money)
    average_cost_per_day_last_month: Mapped[Optional[Decimal]] = mapped_column(Money)
    average_cost_per_day_one_year_ago: Mapped[Optional[Decimal]] = mapped_column(Money)

    bill: Mapped["Bill"] = relationship(back_populates="snapshots")


class ParserWarning(Base):
    __tablename__ = "parser_warnings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bill_id: Mapped[int] = mapped_column(
        ForeignKey("bills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    warning_code: Mapped[Optional[str]] = mapped_column(String(64))
    warning_message: Mapped[Optional[str]] = mapped_column(Text)
    severity: Mapped[Optional[str]] = mapped_column(String(16))

    bill: Mapped["Bill"] = relationship(back_populates="warnings")


class BillAudit(Base):
    """Persisted deterministic audit result for a bill (latest run; one row per bill)."""

    __tablename__ = "bill_audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bill_id: Mapped[int] = mapped_column(
        ForeignKey("bills.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    audit_version: Mapped[Optional[str]] = mapped_column(String(32))
    overall_status: Mapped[Optional[str]] = mapped_column(String(16))
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    bill: Mapped["Bill"] = relationship(back_populates="audit")


class ImportBatch(Base):
    """One bulk-upload run. Records per-batch counts so the UI can show import history."""

    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    total_files: Mapped[int] = mapped_column(Integer, default=0)
    imported_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="processing")

    user: Mapped["User"] = relationship()
    items: Mapped[List["ImportBatchItem"]] = relationship(
        back_populates="import_batch", cascade="all, delete-orphan"
    )
    # Bills keep their FK pointed here; deleting a batch only nulls the link (SET NULL).
    bills: Mapped[List["Bill"]] = relationship(back_populates="import_batch")


class ImportBatchItem(Base):
    """Per-file outcome within an :class:`ImportBatch`."""

    __tablename__ = "import_batch_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    import_batch_id: Mapped[int] = mapped_column(
        ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # imported|duplicate|failed|warning
    bill_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("bills.id", ondelete="SET NULL"), nullable=True, index=True
    )
    error_code: Mapped[Optional[str]] = mapped_column(String(64))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    parser_confidence: Mapped[Optional[str]] = mapped_column(String(16))
    warning_count: Mapped[int] = mapped_column(Integer, default=0)

    import_batch: Mapped["ImportBatch"] = relationship(back_populates="items")
    bill: Mapped[Optional["Bill"]] = relationship()


class ParserRun(Base):
    """One execution of a provider parser against a bill's stored PDF.

    Every initial ingest and every reprocess writes a row here, giving an auditable history
    of which parser version + output schema produced (or attempted to produce) the bill's
    data, when, how long it took, and whether it succeeded. The bill's *current* data always
    reflects the latest successful run; prior runs are retained here for auditability.
    """

    __tablename__ = "parser_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bill_id: Mapped[int] = mapped_column(
        ForeignKey("bills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_name: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[Optional[str]] = mapped_column(String(32))
    parser_schema_version: Mapped[Optional[str]] = mapped_column(String(48))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), nullable=False)  # completed|completed_with_warnings|failed
    warning_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[Optional[str]] = mapped_column(String(64))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    parse_duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    # Distinguishes the first parse from later reprocesses in the UI/history.
    trigger: Mapped[Optional[str]] = mapped_column(String(16))  # ingest|reprocess|backfill

    bill: Mapped["Bill"] = relationship(back_populates="parser_runs")


class BillFieldCorrection(Base):
    """A user-supplied correction of one parsed field, preserving the original value.

    Corrections are an *overlay*: the parsed value stays untouched in its ``bills`` (or
    service) column, and this row records the original + corrected value plus who changed
    it and why. Effective (display/audit/analytics) data merges the latest correction per
    ``field_path`` over the parsed original — see ``correction_service``.
    """

    __tablename__ = "bill_field_corrections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bill_id: Mapped[int] = mapped_column(
        ForeignKey("bills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    field_path: Mapped[str] = mapped_column(String(128), nullable=False)
    original_value: Mapped[Optional[str]] = mapped_column(Text)
    corrected_value: Mapped[Optional[str]] = mapped_column(Text)
    corrected_value_type: Mapped[Optional[str]] = mapped_column(String(16))  # date|int|decimal
    reason: Mapped[Optional[str]] = mapped_column(Text)
    corrected_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    bill: Mapped["Bill"] = relationship(back_populates="corrections")


# ======================================================================================
# Weather (context for usage — never used to assert causality)
# ======================================================================================


class SecurityAuditEvent(Base):
    """Append-only audit trail of security-relevant actions.

    Records *what happened* (event/object) for accountability — never sensitive values.
    ``metadata_json`` holds only non-sensitive context (counts, filenames, statuses).
    """

    __tablename__ = "security_audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    object_type: Mapped[Optional[str]] = mapped_column(String(32))
    object_id: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class WeatherLocation(Base):
    """A geographic point (lat/lon) a user has configured for weather context.

    No address/PII is stored here — only coordinates, a free-text label, and an IANA
    timezone. Weather observations are cached per location/date so the same day is never
    fetched twice.
    """

    __tablename__ = "weather_locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[Optional[str]] = mapped_column(String(128))
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="America/Chicago")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user: Mapped["User"] = relationship()
    observations: Mapped[List["WeatherDailyObservation"]] = relationship(
        back_populates="location", cascade="all, delete-orphan"
    )


class WeatherDailyObservation(Base):
    """One day of cached weather for a location. The (location, date) pair is unique so a
    date is fetched from the provider at most once."""

    __tablename__ = "weather_daily_observations"
    __table_args__ = (
        UniqueConstraint("weather_location_id", "date", name="uq_weather_obs_loc_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    weather_location_id: Mapped[int] = mapped_column(
        ForeignKey("weather_locations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    avg_temp_f: Mapped[Optional[float]] = mapped_column(Float)
    min_temp_f: Mapped[Optional[float]] = mapped_column(Float)
    max_temp_f: Mapped[Optional[float]] = mapped_column(Float)
    avg_humidity: Mapped[Optional[float]] = mapped_column(Float)
    precipitation_inches: Mapped[Optional[float]] = mapped_column(Float)
    heating_degree_days_65: Mapped[Optional[float]] = mapped_column(Float)
    cooling_degree_days_65: Mapped[Optional[float]] = mapped_column(Float)
    source: Mapped[Optional[str]] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    location: Mapped["WeatherLocation"] = relationship(back_populates="observations")


class BillWeatherSummary(Base):
    """Weather aggregated over a bill's service period — the enrichment attached to a bill.

    One row per (bill, location); re-syncing upserts it. Degree-day totals and threshold-day
    counts give downstream normalization the context it needs without storing daily rows.
    """

    __tablename__ = "bill_weather_summaries"
    __table_args__ = (
        UniqueConstraint("bill_id", "weather_location_id", name="uq_bill_weather_loc"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bill_id: Mapped[int] = mapped_column(
        ForeignKey("bills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    weather_location_id: Mapped[int] = mapped_column(
        ForeignKey("weather_locations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    service_start_date: Mapped[Optional[date]] = mapped_column(Date)
    service_end_date: Mapped[Optional[date]] = mapped_column(Date)
    days_observed: Mapped[int] = mapped_column(Integer, default=0)
    avg_temp_f: Mapped[Optional[float]] = mapped_column(Float)
    avg_min_temp_f: Mapped[Optional[float]] = mapped_column(Float)
    avg_max_temp_f: Mapped[Optional[float]] = mapped_column(Float)
    total_precipitation_inches: Mapped[Optional[float]] = mapped_column(Float)
    total_heating_degree_days_65: Mapped[Optional[float]] = mapped_column(Float)
    total_cooling_degree_days_65: Mapped[Optional[float]] = mapped_column(Float)
    days_over_95f: Mapped[int] = mapped_column(Integer, default=0)
    days_over_100f: Mapped[int] = mapped_column(Integer, default=0)
    days_below_32f: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    bill: Mapped["Bill"] = relationship(back_populates="weather_summaries")
    location: Mapped["WeatherLocation"] = relationship()
