"""Manual field-correction workflow (non-destructive overlay).

The deterministic parser occasionally misreads a value (e.g. ``172S`` instead of
``1725``). This service lets a user correct a parsed field **without** destroying the
original: the parsed value stays in its ``bills``/service column forever, and each
correction is recorded in ``bill_field_corrections`` with the original + corrected value,
who changed it, and why.

"Effective" data — what display, the audit, and analytics use — is the parsed value with
the latest correction per ``field_path`` applied on top, computed in memory on a
session-*detached* copy of the bill so a correction can never be flushed back over the
original. Derived metrics are recomputed from the corrected values.

Only an allow-listed set of fields is correctable. Sensitive fields (account number,
payment barcode, name, raw PDF text) are explicitly rejected and are never in the
registry, so they cannot be edited through this interface.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Bill, BillAudit, BillFieldCorrection
from ..parsers.coserv_pdf_parser import parse_date
from . import bill_audit_service, bill_service

logger = logging.getLogger("hui.corrections")


# --------------------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------------------


class CorrectionError(Exception):
    """Base class for correction failures (mapped to 4xx in the API layer)."""

    status_code = 400


class UnsupportedFieldError(CorrectionError):
    status_code = 400


class SensitiveFieldError(CorrectionError):
    status_code = 403


class InvalidCorrectionValue(CorrectionError):
    status_code = 400


# --------------------------------------------------------------------------------------
# Type parsing / formatting
# --------------------------------------------------------------------------------------


def _parse_typed(value_type: str, raw: str):
    """Validate and coerce ``raw`` (a string) to ``value_type``; raise on bad format."""
    if raw is None:
        raise InvalidCorrectionValue("A corrected value is required.")
    s = str(raw).strip()
    if s == "":
        raise InvalidCorrectionValue("A corrected value is required.")
    if value_type == "date":
        d = parse_date(s)
        if d is None:
            raise InvalidCorrectionValue(f"'{raw}' is not a valid date (use YYYY-MM-DD).")
        return d
    if value_type == "int":
        try:
            return int(s.replace(",", ""))
        except ValueError:
            raise InvalidCorrectionValue(f"'{raw}' is not a valid whole number.")
    if value_type == "decimal":
        cleaned = s.replace(",", "").replace("$", "")
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            raise InvalidCorrectionValue(f"'{raw}' is not a valid number.")
    raise InvalidCorrectionValue(f"Unknown value type '{value_type}'.")


def _stringify(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


# --------------------------------------------------------------------------------------
# Field registry — what may be corrected, and how to read/write it on a Bill
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldSpec:
    value_type: str
    get: Callable[[Bill], Any]
    set: Callable[[Bill, Any], None]


def _require_service(svc, name: str):
    if svc is None:
        raise InvalidCorrectionValue(f"This bill has no {name} service to correct.")
    return svc


def _bill_field(attr: str, value_type: str) -> FieldSpec:
    return FieldSpec(
        value_type=value_type,
        get=lambda b, a=attr: getattr(b, a),
        set=lambda b, v, a=attr: setattr(b, a, v),
    )


def _service_field(section: str, attr: str, value_type: str) -> FieldSpec:
    svc_attr = f"{section}_service"

    def _get(b, a=attr, s=svc_attr, n=section):
        return getattr(_require_service(getattr(b, s), n), a)

    def _set(b, v, a=attr, s=svc_attr, n=section):
        setattr(_require_service(getattr(b, s), n), a, v)

    return FieldSpec(value_type=value_type, get=_get, set=_set)


# Static (scalar) correctable fields.
_SCALAR_FIELDS: dict[str, FieldSpec] = {
    "account_summary.bill_date": _bill_field("bill_date", "date"),
    "account_summary.due_date": _bill_field("due_date", "date"),
    "account_summary.total_amount_due": _bill_field("total_amount_due", "decimal"),
    "account_summary.electric_service_total": _bill_field("electric_service_total", "decimal"),
    "account_summary.gas_service_total": _bill_field("gas_service_total", "decimal"),
    "electric.kwh_used": _service_field("electric", "kwh_used", "decimal"),
    "electric.billing_days": _service_field("electric", "billing_days", "int"),
    "electric.last_read": _service_field("electric", "last_read", "int"),
    "electric.current_read": _service_field("electric", "current_read", "int"),
    "electric.multiplier": _service_field("electric", "multiplier", "decimal"),
    "electric.total_current_charges": _service_field("electric", "total_current_charges", "decimal"),
    "gas.ccf_used": _service_field("gas", "ccf_used", "decimal"),
    "gas.billing_days": _service_field("gas", "billing_days", "int"),
    "gas.last_read": _service_field("gas", "last_read", "int"),
    "gas.current_read": _service_field("gas", "current_read", "int"),
    "gas.multiplier": _service_field("gas", "multiplier", "decimal"),
    "gas.total_current_charges": _service_field("gas", "total_current_charges", "decimal"),
}

# Conceptual paths that must never be editable here (PII / non-parsed). Listed explicitly
# so the API can return a clear "sensitive" error rather than a generic "unsupported".
_SENSITIVE_PREFIXES = (
    "account_number",
    "account_summary.account_number",
    "barcode",
    "payment_barcode",
    "name",
    "customer_name",
    "raw_text",
    "pdf_text",
    "meter_number",
)

_LINE_ITEM_RE = re.compile(r"^(?P<svc>electric|gas)\.line_items\[(?P<idx>\d+)\]\.amount$")
_SNAPSHOT_RE = re.compile(
    r"^snapshot\.(?P<svc>electric|gas)\.(?P<field>average_cost_per_day_this_month"
    r"|average_cost_per_day_last_month|average_cost_per_day_one_year_ago)$"
)


def _line_items_for(bill: Bill, service_type: str) -> list:
    return [li for li in bill.line_items if li.service_type == service_type]


def _resolve(field_path: str) -> FieldSpec:
    """Return the :class:`FieldSpec` for ``field_path`` or raise.

    Order: sensitive denylist -> static scalars -> indexed line items -> snapshots ->
    unsupported.
    """
    p = (field_path or "").strip()
    low = p.lower()
    if any(low == s or low.startswith(s + ".") or low.startswith(s + "[") for s in _SENSITIVE_PREFIXES):
        raise SensitiveFieldError(
            f"'{field_path}' is a protected field and cannot be corrected here."
        )

    if p in _SCALAR_FIELDS:
        return _SCALAR_FIELDS[p]

    m = _LINE_ITEM_RE.match(p)
    if m:
        svc, idx = m.group("svc"), int(m.group("idx"))

        def _get(b, s=svc, i=idx):
            items = _line_items_for(b, s)
            if i >= len(items):
                raise InvalidCorrectionValue(f"{s} line item #{i} does not exist on this bill.")
            return items[i].amount

        def _set(b, v, s=svc, i=idx):
            items = _line_items_for(b, s)
            if i >= len(items):
                raise InvalidCorrectionValue(f"{s} line item #{i} does not exist on this bill.")
            items[i].amount = v
            items[i].is_credit = v is not None and v < 0

        return FieldSpec(value_type="decimal", get=_get, set=_set)

    m = _SNAPSHOT_RE.match(p)
    if m:
        svc, field = m.group("svc"), m.group("field")

        def _get(b, s=svc, f=field):
            snap = next((sn for sn in b.snapshots if sn.service_type == s), None)
            return getattr(snap, f) if snap else None

        def _set(b, v, s=svc, f=field):
            snap = next((sn for sn in b.snapshots if sn.service_type == s), None)
            if snap is None:
                raise InvalidCorrectionValue(f"This bill has no {s} snapshot to correct.")
            setattr(snap, f, v)

        return FieldSpec(value_type="decimal", get=_get, set=_set)

    raise UnsupportedFieldError(f"'{field_path}' is not a correctable field.")


def supported_field_paths() -> list[str]:
    """The static, always-available correctable paths (line-item/snapshot paths are
    indexed and validated per bill)."""
    return sorted(_SCALAR_FIELDS.keys())


# --------------------------------------------------------------------------------------
# Overlay: apply corrections + recompute derived (in memory only)
# --------------------------------------------------------------------------------------


def _latest_by_path(corrections: list[BillFieldCorrection]) -> dict[str, BillFieldCorrection]:
    latest: dict[str, BillFieldCorrection] = {}
    for c in sorted(corrections, key=lambda x: (x.created_at or datetime.min.replace(tzinfo=timezone.utc), x.id)):
        latest[c.field_path] = c  # later entries win
    return latest


def apply_corrections(bill: Bill, corrections: list[BillFieldCorrection]) -> list[str]:
    """Mutate ``bill`` IN MEMORY to reflect the latest correction per field_path, then
    recompute derived metrics. Returns the list of field_paths actually applied.

    The caller is responsible for ensuring ``bill`` is detached / never committed.
    """
    applied: list[str] = []
    for path, c in _latest_by_path(corrections).items():
        try:
            spec = _resolve(path)
            value = _parse_typed(c.corrected_value_type or spec.value_type, c.corrected_value or "")
            spec.set(bill, value)
            applied.append(path)
        except CorrectionError:
            # A stored correction that no longer resolves (e.g. line item removed) is
            # skipped rather than breaking the whole bill.
            logger.warning("skipping unresolvable correction on %s", path)
    _recompute_derived(bill)
    return applied


def _recompute_derived(bill: Bill) -> None:
    """Recompute the per-service derived metrics from (possibly corrected) values."""
    div = bill_service._safe_div
    e, g = bill.electric_service, bill.gas_service
    if e is not None:
        e.average_kwh_per_day = div(e.kwh_used, e.billing_days, "0.01")
        e.effective_rate_per_kwh = div(e.total_current_charges, e.kwh_used, "0.0001")
        e.average_cost_per_day = div(bill.electric_service_total, e.billing_days, "0.01")
    if g is not None:
        g.average_ccf_per_day = div(g.ccf_used, g.billing_days, "0.01")
        g.effective_rate_per_ccf = div(g.total_current_charges, g.ccf_used, "0.0001")
        g.average_cost_per_day = div(bill.gas_service_total, g.billing_days, "0.01")


def get_corrections(db: Session, bill_id: int) -> list[BillFieldCorrection]:
    return list(
        db.scalars(
            select(BillFieldCorrection)
            .where(BillFieldCorrection.bill_id == bill_id)
            .order_by(BillFieldCorrection.id.asc())
        ).all()
    )


def get_effective_bill_data(
    db: Session, bill_id: int
) -> Optional[tuple[Bill, list[BillFieldCorrection], list[str]]]:
    """Return ``(effective_bill, corrections, corrected_field_paths)`` or ``None``.

    The returned bill is **detached** from the session and carries corrected values +
    recomputed derived metrics. Because it is detached, mutating it never overwrites the
    stored original.
    """
    bill = bill_service.get_bill(db, bill_id)
    if bill is None:
        return None
    corrections = get_corrections(db, bill_id)
    db.expunge(bill)  # cascade-detaches services/line_items/snapshots/warnings/audit
    corrected_paths = apply_corrections(bill, corrections)
    return bill, corrections, corrected_paths


# --------------------------------------------------------------------------------------
# Serialization
# --------------------------------------------------------------------------------------


def _correction_model(c: BillFieldCorrection) -> dict:
    from ..schemas import BillFieldCorrectionModel

    return BillFieldCorrectionModel(
        id=c.id,
        bill_id=c.bill_id,
        field_path=c.field_path,
        original_value=c.original_value,
        corrected_value=c.corrected_value,
        corrected_value_type=c.corrected_value_type,
        reason=c.reason,
        corrected_by_user_id=c.corrected_by_user_id,
        created_at=c.created_at,
    ).model_dump(mode="json")


def serialize_effective_bill(
    bill: Bill, corrections: list[BillFieldCorrection], corrected_paths: list[str]
) -> dict[str, Any]:
    """Standard bill JSON (with corrected values) plus correction metadata for the UI."""
    data = bill_service.serialize_bill(bill)
    data["corrected_fields"] = sorted(set(corrected_paths))
    data["corrections"] = [_correction_model(c) for c in corrections]
    return data


# --------------------------------------------------------------------------------------
# Create a correction + re-run audit
# --------------------------------------------------------------------------------------


def _persist_audit(db: Session, bill_id: int, result) -> None:
    """Upsert the stored BillAudit so it reflects the corrected data. Runs after the
    effective bill is detached, so committing here never touches the original bill."""
    row = db.scalar(select(BillAudit).where(BillAudit.bill_id == bill_id))
    if row is None:
        row = BillAudit(bill_id=bill_id)
        db.add(row)
    row.audit_version = result.audit_version
    row.overall_status = result.overall_status
    row.result_json = result.model_dump(mode="json")
    row.created_at = datetime.now(timezone.utc)
    db.commit()


def create_correction(
    db: Session,
    bill_id: int,
    *,
    field_path: str,
    corrected_value: str,
    reason: Optional[str],
    user_id: Optional[int],
) -> dict[str, Any]:
    """Validate + store a correction, then return the recomputed effective bill and a
    freshly re-run audit. Raises :class:`CorrectionError` subclasses for bad input."""
    bill = bill_service.get_bill(db, bill_id)
    if bill is None:
        raise CorrectionError("Bill not found.")  # 404-ish; caller maps via status
        # (kept as 400-base; the endpoint checks existence first for a true 404)

    spec = _resolve(field_path)  # raises Unsupported/Sensitive
    typed = _parse_typed(spec.value_type, corrected_value)  # raises Invalid

    # Original value for this correction = the value it replaces: the previous correction's
    # corrected value if one exists, otherwise the parsed original from the stored bill.
    prior = db.scalar(
        select(BillFieldCorrection)
        .where(
            BillFieldCorrection.bill_id == bill_id,
            BillFieldCorrection.field_path == field_path,
        )
        .order_by(BillFieldCorrection.id.desc())
        .limit(1)
    )
    if prior is not None:
        original_value = prior.corrected_value
    else:
        try:
            original_value = _stringify(spec.get(bill))
        except CorrectionError:
            original_value = None

    correction = BillFieldCorrection(
        bill_id=bill_id,
        field_path=field_path,
        original_value=original_value,
        corrected_value=_stringify(typed),
        corrected_value_type=spec.value_type,
        reason=reason,
        corrected_by_user_id=user_id,
    )
    db.add(correction)
    db.commit()
    db.refresh(correction)
    correction_dict = _correction_model(correction)

    # Recompute effective bill + re-run the audit on the corrected data.
    effective, corrections, corrected_paths = get_effective_bill_data(db, bill_id)
    new_audit = bill_audit_service.run_audit(effective)
    _persist_audit(db, bill_id, new_audit)

    logger.info(
        "correction applied: bill=%s field=%s type=%s",  # no values logged (may be PII-adjacent)
        bill_id, field_path, spec.value_type,
    )
    return {
        "success": True,
        "correction": correction_dict,
        "updated_bill": serialize_effective_bill(effective, corrections, corrected_paths),
        "new_audit_result": new_audit.model_dump(mode="json"),
    }
