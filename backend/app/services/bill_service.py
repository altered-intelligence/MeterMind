"""Bill ingestion and read services over the normalized schema.

Ingestion is deterministic: store the PDF, run the deterministic parser, hash PII, and
write the parsed data across the relational tables (bill + electric/gas service + line
items + snapshots + warnings), computing and persisting per-service analytics.

No account numbers, names, addresses, or raw PDF text are ever logged.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..crypto import (
    hash_sensitive_value,
    last4,
    redact_account_number,
    redact_service_address,
)
from ..errors import DuplicateBillError
from ..database import DEFAULT_USER_EMAIL
from ..money import safe_div as _safe_div  # shared Decimal division guard
from ..models import (
    Bill,
    BillSnapshot,
    ChargeLineItem,
    ElectricService,
    GasService,
    ImportBatch,
    ImportBatchItem,
    ParserRun,
    ParserWarning,
    User,
    UtilityAccount,
)
from ..parsers import registry as parser_registry
from ..parsers.coserv_pdf_parser import (
    SCHEMA_VERSION,
    extract_text,
    parse_coserv_bill,
    redacted_debug_blocks,
)
from ..schemas import (
    CorrectionImpact,
    ImportBatchItemResult,
    ImportBatchResult,
    ParserRunModel,
    ReprocessResponse,
    compute_derived,
)
from . import bill_audit_service
from ..storage import get_storage

PROVIDER = "CoServ"
# Registry key for the CoServ parser (the registry normalizes case; bills store "CoServ").
PROVIDER_KEY = "coserv"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB per file


class ReprocessError(Exception):
    """Reprocessing could not run (no stored PDF, unknown parser version, parse failure)."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code

logger = logging.getLogger("hui.bills")

# --------------------------------------------------------------------------------------
# Serialization helpers (Decimal -> JSON-safe string; usage -> number; date -> ISO)
# --------------------------------------------------------------------------------------


def d_str(value: Optional[Decimal]) -> Optional[str]:
    """Decimal -> string (precision preserved), or None."""
    return None if value is None else str(value)


def usage_num(value: Optional[Decimal]):
    """Usage Decimal -> int when whole, else float; or None."""
    if value is None:
        return None
    dec = value if isinstance(value, Decimal) else Decimal(str(value))
    return int(dec) if dec == dec.to_integral_value() else float(dec)


def iso(value: Optional[date]) -> Optional[str]:
    return value.isoformat() if value else None


def _to_dec(value) -> Optional[Decimal]:
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))




# --------------------------------------------------------------------------------------
# Lookups / upserts
# --------------------------------------------------------------------------------------


def get_or_create_default_user(db: Session) -> User:
    user = db.scalar(select(User).where(User.email == DEFAULT_USER_EMAIL))
    if user is None:
        user = User(email=DEFAULT_USER_EMAIL)
        db.add(user)
        db.flush()
    return user


def _get_or_create_account(
    db: Session, user: User, account_number: Optional[str], service_address: Optional[str]
) -> Optional[UtilityAccount]:
    acct_hash = hash_sensitive_value(account_number)
    if acct_hash is None:
        return None
    acct = db.scalar(
        select(UtilityAccount).where(
            UtilityAccount.user_id == user.id,
            UtilityAccount.provider_name == PROVIDER,
            UtilityAccount.account_number_hash == acct_hash,
        )
    )
    if acct is None:
        acct = UtilityAccount(
            user_id=user.id,
            provider_name=PROVIDER,
            account_number_hash=acct_hash,
            account_number_last4=last4(account_number),
            service_address_redacted=redact_service_address(service_address),
        )
        db.add(acct)
        db.flush()
    elif not acct.service_address_redacted and service_address:
        acct.service_address_redacted = redact_service_address(service_address)
    return acct


def _line_raw(name: str, qty, unit, rate, amount) -> str:
    parts = [name]
    if qty is not None and unit:
        parts.append(f"{usage_num(_to_dec(qty))} {unit}")
    if rate is not None:
        parts.append(f"@ {rate}")
    if amount is not None:
        parts.append(f"${amount}")
    return " ".join(parts)


# --------------------------------------------------------------------------------------
# Deduplication
# --------------------------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _find_duplicate(
    db: Session,
    user_id: int,
    *,
    pdf_hash: Optional[str],
    account: Optional[UtilityAccount],
    bill_date: Optional[date],
    total_amount_due: Optional[Decimal],
) -> Optional[Bill]:
    """Return the existing bill this upload duplicates, or ``None``.

    Deterministic and explainable — two independent rules, checked in order:

    1. **Exact file**: the same ``pdf_sha256_hash`` is already stored (a byte-identical
       re-upload, regardless of filename).
    2. **Same statement**: same provider + account (``account_number_hash``) + statement
       ``bill_date`` + ``total_amount_due``. This catches a re-downloaded/re-rendered PDF
       (different bytes) of a bill we already have.
    """
    if pdf_hash:
        hit = db.scalar(
            select(Bill)
            .where(Bill.user_id == user_id, Bill.pdf_sha256_hash == pdf_hash)
            .limit(1)
        )
        if hit is not None:
            return hit

    # Composite rule needs all four signals to be present to assert a match.
    if account is not None and bill_date is not None and total_amount_due is not None:
        hit = db.scalar(
            select(Bill)
            .where(
                Bill.user_id == user_id,
                Bill.provider_name == PROVIDER,
                Bill.utility_account_id == account.id,
                Bill.bill_date == bill_date,
                Bill.total_amount_due == total_amount_due,
            )
            .limit(1)
        )
        if hit is not None:
            return hit
    return None


def _duplicate_reason(existing: Bill, *, by_pdf_hash: bool) -> str:
    if by_pdf_hash:
        return (
            f"Identical PDF already imported (matching file hash) as bill #{existing.id}"
            + (f" dated {existing.bill_date.isoformat()}" if existing.bill_date else "")
            + "."
        )
    date_str = existing.bill_date.isoformat() if existing.bill_date else "unknown date"
    total = existing.total_amount_due
    return (
        f"A {PROVIDER} bill for this account dated {date_str}"
        + (f" totaling ${total}" if total is not None else "")
        + f" already exists as bill #{existing.id}."
    )


# --------------------------------------------------------------------------------------
# Parsed-content -> Bill (shared by initial ingest and reprocess)
# --------------------------------------------------------------------------------------


def _electric_from_parsed(e, derived) -> ElectricService:
    svc = ElectricService(
        service_rate=e.service_rate,
        meter_number_hash=hash_sensitive_value(e.meter_number),
        meter_number_last4=last4(e.meter_number),
        service_start_date=e.service_start_date,
        service_end_date=e.service_end_date,
        billing_days=e.billing_days,
        last_read=e.last_read,
        current_read=e.current_read,
        multiplier=_to_dec(e.multiplier),
        kwh_used=_to_dec(e.kwh_used),
        total_current_charges=_to_dec(e.total_current_charges),
        average_kwh_per_day=derived.electric_avg_kwh_per_day,
        effective_rate_per_kwh=derived.effective_electric_rate,
        average_cost_per_day=derived.electric_avg_cost_per_day,
    )
    return svc


def _gas_from_parsed(g, derived) -> GasService:
    return GasService(
        service_rate=g.service_rate,
        meter_number_hash=hash_sensitive_value(g.meter_number),
        meter_number_last4=last4(g.meter_number),
        service_start_date=g.service_start_date,
        service_end_date=g.service_end_date,
        billing_days=g.billing_days,
        last_read=g.last_read,
        current_read=g.current_read,
        multiplier=_to_dec(g.multiplier),
        ccf_used=_to_dec(g.ccf_used),
        total_current_charges=_to_dec(g.total_current_charges),
        average_ccf_per_day=derived.gas_avg_ccf_per_day,
        effective_rate_per_ccf=derived.effective_gas_rate,
        average_cost_per_day=derived.gas_avg_cost_per_day,
    )


def _line_item_from_parsed(service_type: str, li) -> ChargeLineItem:
    return ChargeLineItem(
        service_type=service_type,
        label=li.name,
        quantity=_to_dec(li.quantity),
        unit=li.unit,
        rate=_to_dec(li.rate),
        amount=_to_dec(li.amount),
        is_credit=li.amount is not None and li.amount < 0,
        raw_text=_line_raw(li.name, li.quantity, li.unit, li.rate, li.amount),
    )


def _fill_bill_content(bill: Bill, parsed, *, schema_version: str) -> None:
    """Populate ``bill``'s parsed scalars, services, line items, snapshots, and warnings
    from ``parsed``. Used by initial ingest AND reprocess.

    On reprocess the existing child collections are *replaced* (delete-orphan removes the
    old rows). Identity/provenance columns (user, account, storage, hashes, batch) and the
    timing/run columns are managed by the caller — never touched here. Manual corrections
    live in a separate table and are intentionally left untouched.
    """
    derived = compute_derived(parsed)
    s = parsed.account_summary
    e = parsed.electric_service
    g = parsed.gas_service

    period_start = (e.service_start_date if e else None) or (g.service_start_date if g else None)
    period_end = (e.service_end_date if e else None) or (g.service_end_date if g else None)
    billing_days = (e.billing_days if e else None) or (g.billing_days if g else None)

    bill.bill_date = s.bill_date
    bill.due_date = s.due_date
    bill.billing_period_start = period_start
    bill.billing_period_end = period_end
    bill.billing_days = billing_days
    bill.previous_balance = _to_dec(s.previous_balance)
    bill.payment_received = _to_dec(s.payment_received)
    bill.balance_forward = _to_dec(s.balance_forward)
    bill.electric_service_total = _to_dec(s.electric_service_total)
    bill.gas_service_total = _to_dec(s.gas_service_total)
    bill.current_charges_total = _to_dec(s.current_charges_total)
    bill.total_amount_due = _to_dec(s.total_amount_due)
    bill.amount_due_after_due_date = _to_dec(s.amount_due_after_due_date)
    bill.autopay_status = s.autopay_status
    bill.parser_version = parsed.parser_version
    bill.parser_confidence = parsed.parser_confidence.value
    bill.parser_schema_version = schema_version

    # Replace child collections (delete-orphan cleans up any prior rows on reprocess).
    bill.electric_service = _electric_from_parsed(e, derived) if e is not None else None
    bill.gas_service = _gas_from_parsed(g, derived) if g is not None else None

    line_items: list[ChargeLineItem] = []
    if e is not None:
        line_items += [_line_item_from_parsed("electric", li) for li in e.line_items]
    if g is not None:
        line_items += [_line_item_from_parsed("gas", li) for li in g.line_items]
    bill.line_items = line_items

    snap = parsed.snapshot
    snapshots: list[BillSnapshot] = []
    if snap is not None:
        snapshots.append(
            BillSnapshot(
                service_type="electric",
                average_cost_per_day_this_month=snap.electric_average_cost_per_day_this_month,
                average_cost_per_day_last_month=snap.electric_average_cost_per_day_last_month,
                average_cost_per_day_one_year_ago=snap.electric_average_cost_per_day_one_year_ago,
            )
        )
        snapshots.append(
            BillSnapshot(
                service_type="gas",
                average_cost_per_day_this_month=snap.gas_average_cost_per_day_this_month,
                average_cost_per_day_last_month=snap.gas_average_cost_per_day_last_month,
                average_cost_per_day_one_year_ago=snap.gas_average_cost_per_day_one_year_ago,
            )
        )
    bill.snapshots = snapshots

    bill.warnings = [
        ParserWarning(
            warning_code=w.code,
            warning_message=w.message + (f" [{w.field}]" if w.field else ""),
            severity=w.severity,
        )
        for w in parsed.warnings
    ]


def _record_parser_run(
    db: Session,
    bill_id: int,
    *,
    provider_name: str,
    parser_version: Optional[str],
    schema_version: Optional[str],
    started_at: datetime,
    completed_at: datetime,
    status: str,
    warning_count: int,
    parse_duration_ms: Optional[int],
    trigger: str,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
) -> ParserRun:
    """Persist one auditable parser execution. Caller commits."""
    run = ParserRun(
        bill_id=bill_id,
        provider_name=provider_name,
        parser_version=parser_version,
        parser_schema_version=schema_version,
        started_at=started_at,
        completed_at=completed_at,
        status=status,
        warning_count=warning_count,
        parse_duration_ms=parse_duration_ms,
        trigger=trigger,
        error_code=error_code,
        error_message=error_message,
    )
    db.add(run)
    return run


# --------------------------------------------------------------------------------------
# Ingestion
# --------------------------------------------------------------------------------------


def ingest_bill(
    db: Session,
    *,
    file_bytes: bytes,
    filename: str,
    import_batch_id: Optional[int] = None,
) -> Bill:
    pdf_hash = _sha256(file_bytes)
    user = get_or_create_default_user(db)

    # Cheapest dedupe first: a byte-identical PDF needs no parsing or storage.
    existing = db.scalar(
        select(Bill)
        .where(Bill.user_id == user.id, Bill.pdf_sha256_hash == pdf_hash)
        .limit(1)
    )
    if existing is not None:
        raise DuplicateBillError(
            _duplicate_reason(existing, by_pdf_hash=True), existing_bill_id=existing.id
        )

    storage = get_storage()
    key = storage.save(file_bytes, suffix=".pdf")
    try:
        started_at = datetime.now(timezone.utc)
        t0 = time.perf_counter()
        parsed = parse_coserv_bill(storage.path(key))
        parsed_text_hash = _sha256(extract_text(storage.path(key)).encode("utf-8"))
        parse_duration_ms = int((time.perf_counter() - t0) * 1000)
        completed_at = datetime.now(timezone.utc)
        s = parsed.account_summary

        acct = _get_or_create_account(db, user, s.account_number, s.service_address)

        # Composite duplicate guard: same provider + account + bill_date + total.
        dup = _find_duplicate(
            db,
            user.id,
            pdf_hash=None,  # pdf-hash path already checked pre-save
            account=acct,
            bill_date=s.bill_date,
            total_amount_due=_to_dec(s.total_amount_due),
        )
        if dup is not None:
            raise DuplicateBillError(
                _duplicate_reason(dup, by_pdf_hash=False), existing_bill_id=dup.id
            )

        bill = Bill(
            user_id=user.id,
            utility_account_id=acct.id if acct else None,
            provider_name=PROVIDER,
            pdf_storage_path=key,
            pdf_sha256_hash=pdf_hash,
            parsed_text_sha256_hash=parsed_text_hash,
            source_filename=filename,
            import_batch_id=import_batch_id,
            parsed_at=completed_at,
            parse_duration_ms=parse_duration_ms,
        )
        # Populate parsed scalars, services, line items, snapshots, warnings.
        _fill_bill_content(bill, parsed, schema_version=SCHEMA_VERSION)

        db.add(bill)
        db.flush()  # assign bill.id + persist children so the audit can read them

        run_status = "completed_with_warnings" if parsed.warnings else "completed"
        _record_parser_run(
            db,
            bill.id,
            provider_name=PROVIDER,
            parser_version=parsed.parser_version,
            schema_version=SCHEMA_VERSION,
            started_at=started_at,
            completed_at=completed_at,
            status=run_status,
            warning_count=len(parsed.warnings),
            parse_duration_ms=parse_duration_ms,
            trigger="ingest",
        )

        # Run the deterministic audit. Never blocks ingestion — log and continue on error.
        try:
            bill_audit_service.attach_audit(bill)
        except Exception:
            logger.warning("Bill audit failed (non-blocking); it can be computed on demand.")
        db.commit()
        db.refresh(bill)
        # Redacted, PII-free ingest log (no raw text, account #, address, or meter #).
        logger.info(
            "bill ingested: success=true parser_version=%s bill_date=%s account=%s "
            "confidence=%s warnings=%d",
            parsed.parser_version,
            s.bill_date.isoformat() if s.bill_date else "unknown",
            redact_account_number(s.account_number) or "none",
            parsed.parser_confidence.value,
            len(parsed.warnings),
        )
        return bill
    except Exception:
        db.rollback()
        storage.delete(key)
        raise


# --------------------------------------------------------------------------------------
# Reprocessing (re-run a (newer) parser over a bill's stored PDF)
# --------------------------------------------------------------------------------------


def _parser_run_model(run: ParserRun) -> ParserRunModel:
    return ParserRunModel(
        id=run.id,
        bill_id=run.bill_id,
        provider_name=run.provider_name,
        parser_version=run.parser_version,
        parser_schema_version=run.parser_schema_version,
        started_at=run.started_at,
        completed_at=run.completed_at,
        status=run.status,
        warning_count=run.warning_count,
        error_code=run.error_code,
        error_message=run.error_message,
        parse_duration_ms=run.parse_duration_ms,
        trigger=run.trigger,
    )


def parser_runs_for_bill(db: Session, bill_id: int) -> list[ParserRun]:
    return list(
        db.scalars(
            select(ParserRun)
            .where(ParserRun.bill_id == bill_id)
            .order_by(ParserRun.id.asc())
        ).all()
    )


def _correction_impacts(bill: Bill) -> list[CorrectionImpact]:
    """For each distinct corrected field_path, check whether it still resolves against the
    freshly-parsed bill content. A path that no longer resolves (e.g. a line item that the
    new parser no longer emits) is flagged so the user can review it; the correction row is
    never deleted."""
    from . import correction_service  # local import avoids an import cycle

    impacts: list[CorrectionImpact] = []
    seen: set[str] = set()
    for c in sorted(bill.corrections, key=lambda x: x.id):
        if c.field_path in seen:
            continue
        seen.add(c.field_path)
        try:
            spec = correction_service._resolve(c.field_path)
            spec.get(bill)  # raises if the path is gone on the new content
            impacts.append(CorrectionImpact(field_path=c.field_path, still_applies=True))
        except correction_service.CorrectionError as exc:
            impacts.append(
                CorrectionImpact(field_path=c.field_path, still_applies=False, note=str(exc))
            )
    return impacts


def reprocess_bill(
    db: Session, bill_id: int, *, target_parser_version: Optional[str] = None
) -> Optional[ReprocessResponse]:
    """Re-run a parser over the bill's stored PDF and update its current parsed data.

    Non-destructive in the ways that matter: manual corrections (a separate table) are
    preserved and re-applied on read; every run is recorded in ``parser_runs`` for an
    auditable history; the prior parser version/schema are returned for reference. Raises
    :class:`ReprocessError` (no stored PDF / unknown version / parse failure)."""
    bill = get_bill(db, bill_id)
    if bill is None:
        return None
    if not bill.pdf_storage_path:
        raise ReprocessError("This bill has no stored PDF to reprocess.", status_code=409)
    path = get_storage().path(bill.pdf_storage_path)
    if not Path(path).exists():
        raise ReprocessError("The stored PDF for this bill is no longer available.", status_code=409)

    try:
        parser = (
            parser_registry.get_parser(PROVIDER_KEY, target_parser_version)
            if target_parser_version
            else parser_registry.get_latest_parser(PROVIDER_KEY)
        )
    except parser_registry.UnknownParserError as exc:
        raise ReprocessError(str(exc), status_code=400)

    prev_version = bill.parser_version
    prev_schema = bill.parser_schema_version

    started_at = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    try:
        parsed = parser.parse(path)
        parsed_text_hash = _sha256(parser.extract_text(path).encode("utf-8"))
    except Exception:
        # Record the failed attempt for auditability, then surface a clean error.
        parse_duration_ms = int((time.perf_counter() - t0) * 1000)
        completed_at = datetime.now(timezone.utc)
        logger.exception("reprocess: parser failed for bill %s", bill_id)  # stack only, no PII
        _record_parser_run(
            db,
            bill_id,
            provider_name=parser.provider_name,
            parser_version=parser.parser_version,
            schema_version=parser.schema_version,
            started_at=started_at,
            completed_at=completed_at,
            status="failed",
            warning_count=0,
            parse_duration_ms=parse_duration_ms,
            trigger="reprocess",
            error_code="parser_failed",
            error_message="The stored PDF could not be reparsed.",
        )
        db.commit()
        raise ReprocessError("The stored PDF could not be reparsed.", status_code=422)

    parse_duration_ms = int((time.perf_counter() - t0) * 1000)
    completed_at = datetime.now(timezone.utc)

    # Update the bill's current parsed data in place. Clear the old children and flush the
    # DELETEs FIRST: the electric/gas services have a UNIQUE(bill_id), so reassigning them
    # in one flush would try to INSERT the replacement before deleting the original and trip
    # the constraint. Corrections live in a separate table and are untouched.
    bill.electric_service = None
    bill.gas_service = None
    bill.line_items = []
    bill.snapshots = []
    bill.warnings = []
    db.flush()
    _fill_bill_content(bill, parsed, schema_version=parser.schema_version)
    bill.parsed_text_sha256_hash = parsed_text_hash
    bill.parsed_at = completed_at
    bill.parse_duration_ms = parse_duration_ms

    # Which corrections still line up with the new content? (Corrections are preserved.)
    impacts = _correction_impacts(bill)

    run_status = "completed_with_warnings" if parsed.warnings else "completed"
    run = _record_parser_run(
        db,
        bill_id,
        provider_name=parser.provider_name,
        parser_version=parser.parser_version,
        schema_version=parser.schema_version,
        started_at=started_at,
        completed_at=completed_at,
        status=run_status,
        warning_count=len(parsed.warnings),
        parse_duration_ms=parse_duration_ms,
        trigger="reprocess",
    )
    db.flush()
    run_model = _parser_run_model(run)
    db.commit()

    # Recompute the effective (corrected) bill + re-run and persist the audit on it.
    from . import correction_service  # local import avoids an import cycle

    effective, corrections, corrected_paths = correction_service.get_effective_bill_data(db, bill_id)
    new_audit = bill_audit_service.run_audit(effective)
    correction_service._persist_audit(db, bill_id, new_audit)

    warnings: list[str] = [
        f"Correction on '{i.field_path}' may no longer apply: {i.note}"
        for i in impacts
        if not i.still_applies
    ]

    logger.info(
        "bill reprocessed: bill=%s parser_version=%s->%s confidence=%s warnings=%d corrections=%d",
        bill_id, prev_version, parser.parser_version, parsed.parser_confidence.value,
        len(parsed.warnings), len(impacts),
    )

    return ReprocessResponse(
        success=True,
        bill_id=bill_id,
        parser_run=run_model,
        previous_parser_version=prev_version,
        previous_parser_schema_version=prev_schema,
        updated_bill=correction_service.serialize_effective_bill(effective, corrections, corrected_paths),
        new_audit_result=new_audit.model_dump(mode="json"),
        correction_warnings=impacts,
        warnings=warnings,
    )


# --------------------------------------------------------------------------------------
# Bulk ingestion (multi-file import with per-file results)
# --------------------------------------------------------------------------------------


def _validate_pdf_bytes(data: bytes) -> Optional[str]:
    """Return an error message if ``data`` is not an acceptable PDF, else ``None``."""
    if not data:
        return "Uploaded file is empty."
    if len(data) > MAX_UPLOAD_BYTES:
        return "File too large (max 20 MB)."
    if not data[:5].startswith(b"%PDF"):
        return "File is not a valid PDF."
    return None


def _record_item(
    db: Session,
    batch_id: int,
    filename: str,
    *,
    status: str,
    bill_id: Optional[int] = None,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
    parser_confidence: Optional[str] = None,
    warning_count: int = 0,
) -> ImportBatchItem:
    """Persist one per-file outcome. Committed independently so a later file's failure
    never rolls back an already-recorded result."""
    item = ImportBatchItem(
        import_batch_id=batch_id,
        filename=filename,
        status=status,
        bill_id=bill_id,
        error_code=error_code,
        error_message=error_message,
        parser_confidence=parser_confidence,
        warning_count=warning_count,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _process_one(db: Session, batch_id: int, filename: str, data: bytes) -> ImportBatchItem:
    """Import a single file and record its outcome. Never raises — every path produces an
    :class:`ImportBatchItem` so one bad file can't abort the batch."""
    err = _validate_pdf_bytes(data)
    if err is not None:
        return _record_item(
            db, batch_id, filename, status="failed", error_code="invalid_pdf", error_message=err
        )
    try:
        bill = ingest_bill(db, file_bytes=data, filename=filename, import_batch_id=batch_id)
    except DuplicateBillError as exc:
        return _record_item(
            db,
            batch_id,
            filename,
            status="duplicate",
            bill_id=exc.existing_bill_id,
            error_code="duplicate_bill",
            error_message=exc.reason,
        )
    except Exception:  # noqa: BLE001 — isolate the failure to this file
        logger.exception("bulk import: a file failed to parse/store")  # stack only, no PII
        return _record_item(
            db,
            batch_id,
            filename,
            status="failed",
            error_code="parser_failed",
            error_message="Could not parse the uploaded bill.",
        )
    status = "warning" if bill.warnings else "imported"
    return _record_item(
        db,
        batch_id,
        filename,
        status=status,
        bill_id=bill.id,
        parser_confidence=bill.parser_confidence,
        warning_count=len(bill.warnings),
    )


def bulk_ingest(db: Session, files: list[tuple[str, bytes]]) -> ImportBatch:
    """Process many uploaded PDFs one at a time, recording a per-file result.

    The batch row is committed up-front and each file commits independently, so a single
    failure (bad PDF, parser error) leaves every other file's result intact — the import
    never rolls back as a whole.
    """
    user = get_or_create_default_user(db)
    batch = ImportBatch(user_id=user.id, total_files=len(files), status="processing")
    db.add(batch)
    db.commit()
    db.refresh(batch)
    batch_id = batch.id

    counts = {"imported": 0, "warning": 0, "duplicate": 0, "failed": 0}
    for filename, data in files:
        item = _process_one(db, batch_id, filename, data)
        counts[item.status] = counts.get(item.status, 0) + 1

    batch.imported_count = counts["imported"]
    batch.warning_count = counts["warning"]
    batch.duplicate_count = counts["duplicate"]
    batch.failed_count = counts["failed"]
    batch.completed_at = datetime.now(timezone.utc)
    batch.status = "completed" if counts["failed"] == 0 else "completed_with_errors"
    db.commit()
    db.refresh(batch)
    logger.info(
        "bulk import complete: batch=%s files=%d imported=%d warning=%d duplicate=%d failed=%d",
        batch_id, len(files), counts["imported"], counts["warning"],
        counts["duplicate"], counts["failed"],
    )
    return batch


# --------------------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------------------

_EAGER = (
    selectinload(Bill.electric_service),
    selectinload(Bill.gas_service),
    selectinload(Bill.line_items),
    selectinload(Bill.snapshots),
    selectinload(Bill.warnings),
    selectinload(Bill.utility_account),
    selectinload(Bill.audit),
    selectinload(Bill.corrections),
    selectinload(Bill.parser_runs),
)


def bills_for_user(db: Session, user_id: int, *, apply_corrections: bool = True) -> list[Bill]:
    """All of a user's bills as a chronological series (undated last).

    By default the manual-correction overlay is applied so analytics/list reads reflect
    corrected values. The bills are then *detached* from the session (the overlay mutates
    them in memory only), so the stored originals are never overwritten. Pass
    ``apply_corrections=False`` for the raw, session-attached rows.
    """
    rows = list(
        db.scalars(select(Bill).where(Bill.user_id == user_id).options(*_EAGER)).all()
    )
    ordered = sorted(
        rows, key=lambda r: (r.bill_date is None, r.bill_date or r.created_at.date(), r.id)
    )
    if apply_corrections and ordered:
        from . import correction_service  # local import avoids an import cycle

        for bill in ordered:
            corrections = list(bill.corrections)
            if not corrections:
                continue  # no overlay needed; stored derived values are already correct
            db.expunge(bill)  # detach before mutating so originals stay safe
            correction_service.apply_corrections(bill, corrections)
    return ordered


def list_bills(db: Session) -> list[Bill]:
    user = get_or_create_default_user(db)
    return bills_for_user(db, user.id)


def get_bill(db: Session, bill_id: int) -> Optional[Bill]:
    return db.scalar(select(Bill).where(Bill.id == bill_id).options(*_EAGER))


def delete_bill(db: Session, bill_id: int) -> bool:
    row = db.get(Bill, bill_id)
    if row is None:
        return False
    key = row.pdf_storage_path
    db.delete(row)  # ORM + ON DELETE CASCADE clean up children
    db.commit()
    # DB is the source of truth; PDF cleanup is best-effort so a storage error can't
    # fail the request or leave the bill un-deleted. Orphans are logged for cleanup.
    if key:
        try:
            get_storage().delete(key)
        except Exception:
            logger.warning("Bill %s deleted; stored PDF could not be removed.", bill_id)
    return True


# --------------------------------------------------------------------------------------
# Import-batch reads / serialization
# --------------------------------------------------------------------------------------

_BATCH_EAGER = (
    selectinload(ImportBatch.items)
    .selectinload(ImportBatchItem.bill)
    .selectinload(Bill.warnings),
)


def list_import_batches(db: Session) -> list[ImportBatch]:
    """A user's import batches, newest first."""
    user = get_or_create_default_user(db)
    return list(
        db.scalars(
            select(ImportBatch)
            .where(ImportBatch.user_id == user.id)
            .options(*_BATCH_EAGER)
            .order_by(ImportBatch.id.desc())
        ).all()
    )


def get_import_batch(db: Session, batch_id: int) -> Optional[ImportBatch]:
    return db.scalar(
        select(ImportBatch).where(ImportBatch.id == batch_id).options(*_BATCH_EAGER)
    )


def _item_result(item: ImportBatchItem) -> ImportBatchItemResult:
    bill = item.bill  # may be None (never stored, or later deleted)
    return ImportBatchItemResult(
        filename=item.filename,
        status=item.status,
        bill_id=item.bill_id,
        bill_date=iso(bill.bill_date) if bill else None,
        total_amount_due=d_str(bill.total_amount_due) if bill else None,
        parser_confidence=item.parser_confidence,
        warnings=[w.warning_message or "" for w in bill.warnings] if bill else [],
        error=item.error_message,
    )


def serialize_import_batch(batch: ImportBatch) -> dict[str, Any]:
    items = sorted(batch.items, key=lambda i: i.id)
    return ImportBatchResult(
        id=batch.id,
        status=batch.status,
        started_at=batch.started_at,
        completed_at=batch.completed_at,
        total_files=batch.total_files,
        imported_count=batch.imported_count,
        duplicate_count=batch.duplicate_count,
        failed_count=batch.failed_count,
        warning_count=batch.warning_count,
        items=[_item_result(i) for i in items],
    ).model_dump(mode="json")


# --------------------------------------------------------------------------------------
# Serialization
# --------------------------------------------------------------------------------------


def _line_items(bill: Bill, service_type: str) -> list[dict]:
    return [
        {
            "name": li.label,
            "amount": d_str(li.amount),
            "quantity": d_str(li.quantity),
            "unit": li.unit,
            "rate": d_str(li.rate),
            "is_credit": li.is_credit,
            "currency": "USD",
        }
        for li in bill.line_items
        if li.service_type == service_type
    ]


def _electric_dict(bill: Bill) -> Optional[dict]:
    e = bill.electric_service
    if e is None:
        return None
    return {
        "service_rate": e.service_rate,
        "meter_number_last4": e.meter_number_last4,
        "service_start_date": iso(e.service_start_date),
        "service_end_date": iso(e.service_end_date),
        "billing_days": e.billing_days,
        "last_read": e.last_read,
        "current_read": e.current_read,
        "multiplier": d_str(e.multiplier),
        "kwh_used": usage_num(e.kwh_used),
        "usage_unit": "kWh",
        "total_current_charges": d_str(e.total_current_charges),
        "average_kwh_per_day": d_str(e.average_kwh_per_day),
        "effective_rate_per_kwh": d_str(e.effective_rate_per_kwh),
        "average_cost_per_day": d_str(e.average_cost_per_day),
        "line_items": _line_items(bill, "electric"),
    }


def _gas_dict(bill: Bill) -> Optional[dict]:
    g = bill.gas_service
    if g is None:
        return None
    return {
        "service_rate": g.service_rate,
        "meter_number_last4": g.meter_number_last4,
        "service_start_date": iso(g.service_start_date),
        "service_end_date": iso(g.service_end_date),
        "billing_days": g.billing_days,
        "last_read": g.last_read,
        "current_read": g.current_read,
        "multiplier": d_str(g.multiplier),
        "ccf_used": usage_num(g.ccf_used),
        "usage_unit": "CCF",
        "total_current_charges": d_str(g.total_current_charges),
        "average_ccf_per_day": d_str(g.average_ccf_per_day),
        "effective_rate_per_ccf": d_str(g.effective_rate_per_ccf),
        "average_cost_per_day": d_str(g.average_cost_per_day),
        "line_items": _line_items(bill, "gas"),
    }


def _snapshot_dict(bill: Bill) -> Optional[dict]:
    if not bill.snapshots:
        return None
    out: dict[str, Any] = {}
    for snap in bill.snapshots:
        out[snap.service_type] = {
            "average_cost_per_day_this_month": d_str(snap.average_cost_per_day_this_month),
            "average_cost_per_day_last_month": d_str(snap.average_cost_per_day_last_month),
            "average_cost_per_day_one_year_ago": d_str(snap.average_cost_per_day_one_year_ago),
        }
    return out or None


def _derived_dict(bill: Bill) -> dict:
    e, g = bill.electric_service, bill.gas_service
    return {
        "electric_avg_kwh_per_day": d_str(e.average_kwh_per_day) if e else None,
        "gas_avg_ccf_per_day": d_str(g.average_ccf_per_day) if g else None,
        "effective_electric_rate": d_str(e.effective_rate_per_kwh) if e else None,
        "effective_gas_rate": d_str(g.effective_rate_per_ccf) if g else None,
        "electric_avg_cost_per_day": d_str(e.average_cost_per_day) if e else None,
        "gas_avg_cost_per_day": d_str(g.average_cost_per_day) if g else None,
        "total_avg_cost_per_day": d_str(_safe_div(bill.total_amount_due, bill.billing_days, "0.01")),
        "electric_percentage_of_bill": d_str(
            _safe_div(bill.electric_service_total, bill.current_charges_total)
        ),
        "gas_percentage_of_bill": d_str(
            _safe_div(bill.gas_service_total, bill.current_charges_total)
        ),
    }


def serialize_bill(bill: Bill) -> dict[str, Any]:
    """Full API representation of a bill (PII is hashed/redacted by design — never full)."""
    ua = bill.utility_account
    acct_last4 = ua.account_number_last4 if ua else None
    return {
        "id": bill.id,
        "created_at": bill.created_at.isoformat() if bill.created_at else None,
        "provider_name": bill.provider_name,
        "parser_version": bill.parser_version,
        "parser_schema_version": bill.parser_schema_version,
        "parser_confidence": bill.parser_confidence,
        "parsed_at": bill.parsed_at.isoformat() if bill.parsed_at else None,
        "parse_duration_ms": bill.parse_duration_ms,
        "parser_runs": [
            _parser_run_model(r).model_dump(mode="json")
            for r in sorted(getattr(bill, "parser_runs", []) or [], key=lambda r: r.id)
        ],
        "bill_date": iso(bill.bill_date),
        "due_date": iso(bill.due_date),
        "billing_period_start": iso(bill.billing_period_start),
        "billing_period_end": iso(bill.billing_period_end),
        "billing_days": bill.billing_days,
        "account_summary": {
            "bill_date": iso(bill.bill_date),
            "due_date": iso(bill.due_date),
            "previous_balance": d_str(bill.previous_balance),
            "payment_received": d_str(bill.payment_received),
            "balance_forward": d_str(bill.balance_forward),
            "electric_service_total": d_str(bill.electric_service_total),
            "gas_service_total": d_str(bill.gas_service_total),
            "current_charges_total": d_str(bill.current_charges_total),
            "total_amount_due": d_str(bill.total_amount_due),
            "amount_due_after_due_date": d_str(bill.amount_due_after_due_date),
            "autopay_status": bill.autopay_status,
            "account_number_last4": acct_last4,
            "account_number_redacted": (f"••••{acct_last4}" if acct_last4 else None),
            "service_address_redacted": ua.service_address_redacted if ua else None,
        },
        "utility_account": (
            {
                "id": ua.id,
                "provider_name": ua.provider_name,
                "account_number_last4": ua.account_number_last4,
                "service_address_redacted": ua.service_address_redacted,
            }
            if ua
            else None
        ),
        "electric_service": _electric_dict(bill),
        "gas_service": _gas_dict(bill),
        "snapshot": _snapshot_dict(bill),
        "derived": _derived_dict(bill),
        "warnings": [
            {"code": w.warning_code, "message": w.warning_message, "severity": w.severity}
            for w in bill.warnings
        ],
    }


def export_json(bill: Bill) -> dict[str, Any]:
    """Parsed bill as JSON-compatible data. PII stays hashed/redacted (never full)."""
    return serialize_bill(bill)


def bill_debug_blocks(bill: Bill) -> dict[str, Any]:
    """Redacted extracted-text blocks + warnings/confidence for the debug view."""
    text_blocks = "<stored PDF not found>"
    if bill.pdf_storage_path:
        path = get_storage().path(bill.pdf_storage_path)
        if Path(path).exists():
            text_blocks = redacted_debug_blocks(path)
    return {
        "id": bill.id,
        "parser_version": bill.parser_version,
        "parser_confidence": bill.parser_confidence,
        "warnings": [
            {"code": w.warning_code, "message": w.warning_message, "severity": w.severity}
            for w in bill.warnings
        ],
        "redacted_text_blocks": text_blocks,
    }
