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
    parser_confidence: Mapped[Optional[str]] = mapped_column(String(16))
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
