"""Deterministic bill-audit engine.

Verifies the *internal math* of a parsed CoServ bill — not fraud detection, just
structured consistency checking (sums reconcile, meter deltas match usage, line items add
up, sanity ranges hold). Every check is computed deterministically from already-parsed,
validated structured data on the :class:`Bill` ORM row.

An audit never blocks ingestion: failures/warnings are recorded, not raised.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from ..models import Bill, BillAudit
from ..schemas import BillAuditCheck, BillAuditResult, BillAuditSeverity, BillAuditStatus

AUDIT_VERSION = "audit-v0.1.0"

MONEY_TOL = Decimal("0.01")
METER_TOL = Decimal("0.5")
SNAPSHOT_TOL = Decimal("0.10")

# shorthands
_PASS, _WARN, _FAIL, _SKIP = (
    BillAuditStatus.PASS,
    BillAuditStatus.WARNING,
    BillAuditStatus.FAIL,
    BillAuditStatus.SKIPPED,
)
_INFO, _LOW, _MED, _HIGH = (
    BillAuditSeverity.INFO,
    BillAuditSeverity.LOW,
    BillAuditSeverity.MEDIUM,
    BillAuditSeverity.HIGH,
)


# --------------------------------------------------------------------------------------
# Builders / helpers
# --------------------------------------------------------------------------------------


def _dec(v) -> Optional[Decimal]:
    if v is None:
        return None
    return v if isinstance(v, Decimal) else Decimal(str(v))


def _s(v) -> Optional[str]:
    return None if v is None else str(v)


def _check(code, label, group, status, severity, *, expected=None, actual=None,
           difference=None, tolerance=None, message="", related=()) -> BillAuditCheck:
    return BillAuditCheck(
        check_code=code,
        label=label,
        group=group,
        status=status,
        severity=severity,
        expected_value=_s(expected),
        actual_value=_s(actual),
        difference=_s(difference),
        tolerance=_s(tolerance),
        message=message,
        related_fields=list(related),
    )


def _skipped(code, label, group, severity, related, msg="Insufficient data to run this check."):
    return _check(code, label, group, _SKIP, severity, message=msg, related=related)


def _equality(code, label, group, severity, *, expected, actual, tolerance, related,
              fail_on_violation, unit="$") -> BillAuditCheck:
    """Pass when |actual - expected| <= tolerance; otherwise fail or warn."""
    e, a = _dec(expected), _dec(actual)
    if e is None or a is None:
        return _skipped(code, label, group, severity, related)
    diff = a - e
    tol = Decimal(tolerance)

    def fmt(x: Decimal) -> str:
        return f"${x}" if unit == "$" else f"{x} {unit}".strip()

    if abs(diff) <= tol:
        return _check(code, label, group, _PASS, severity, expected=e, actual=a,
                      difference=diff, tolerance=tol, message="Consistent.", related=related)
    status = _FAIL if fail_on_violation else _WARN
    msg = f"Expected {fmt(e)} but found {fmt(a)} (off by {fmt(diff)}; tolerance {fmt(tol)})."
    return _check(code, label, group, status, severity, expected=e, actual=a,
                  difference=diff, tolerance=tol, message=msg, related=related)


def _line_total(bill: Bill, service_type: str) -> Optional[Decimal]:
    items = [li for li in bill.line_items if li.service_type == service_type]
    if not items:
        return None
    total = Decimal(0)
    for li in items:
        if li.amount is not None:
            total += Decimal(li.amount)  # credits are already negative -> they subtract
    return total


def _snapshot_value(bill: Bill, service_type: str) -> Optional[Decimal]:
    for snap in bill.snapshots:
        if snap.service_type == service_type:
            return _dec(snap.average_cost_per_day_this_month)
    return None


# --------------------------------------------------------------------------------------
# Check groups
# --------------------------------------------------------------------------------------


def _account_summary_checks(bill: Bill) -> list[BillAuditCheck]:
    g = "account_summary"
    checks: list[BillAuditCheck] = []

    elec, gas = _dec(bill.electric_service_total), _dec(bill.gas_service_total)
    checks.append(
        _equality(
            "electric_plus_gas_equals_current_charges",
            "Electric + gas equals current charges",
            g, _HIGH,
            expected=bill.current_charges_total,
            actual=(elec + gas) if (elec is not None and gas is not None) else None,
            tolerance=MONEY_TOL,
            related=["electric_service_total", "gas_service_total", "current_charges_total"],
            fail_on_violation=True,
        )
    )

    cc, bf = _dec(bill.current_charges_total), _dec(bill.balance_forward)
    checks.append(
        _equality(
            "current_charges_plus_balance_forward_equals_total_due",
            "Current charges + balance forward equals total due",
            g, _MED,
            expected=bill.total_amount_due,
            actual=(cc + bf) if (cc is not None and bf is not None) else None,
            tolerance=MONEY_TOL,
            related=["current_charges_total", "balance_forward", "total_amount_due"],
            fail_on_violation=False,  # special adjustments can legitimately differ -> warn
        )
    )

    # Payments clear the previous balance — only meaningful when balance forward is $0.
    prev, pay = _dec(bill.previous_balance), _dec(bill.payment_received)
    code3 = "payment_received_matches_previous_balance_when_balance_forward_zero"
    label3 = "Payments clear the previous balance (when balance forward is $0)"
    rel3 = ["previous_balance", "payment_received", "balance_forward"]
    if bf is None or prev is None or pay is None:
        checks.append(_skipped(code3, label3, g, _MED, rel3))
    elif abs(bf) > MONEY_TOL:
        checks.append(
            _skipped(code3, label3, g, _MED, rel3,
                     msg="Skipped: only applies when balance forward is $0.")
        )
    else:
        # payment_received is stored as a negative credit, so prev + payment should net to 0.
        checks.append(
            _equality(code3, label3, g, _MED, expected=Decimal(0), actual=prev + pay,
                      tolerance=MONEY_TOL, related=rel3, fail_on_violation=False)
        )

    # Late amount >= current amount due.
    late, total = _dec(bill.amount_due_after_due_date), _dec(bill.total_amount_due)
    code4 = "late_amount_greater_than_or_equal_current_amount"
    label4 = "Late amount is at least the current amount due"
    rel4 = ["amount_due_after_due_date", "total_amount_due"]
    if late is None or total is None:
        checks.append(_skipped(code4, label4, g, _LOW, rel4))
    else:
        diff = late - total
        ok = late >= total - MONEY_TOL
        checks.append(
            _check(code4, label4, g, _PASS if ok else _WARN, _LOW,
                   expected=f">= {total}", actual=late, difference=diff,
                   message=("Consistent." if ok
                            else f"Late amount ${late} is less than the current amount due ${total}."),
                   related=rel4)
        )
    return checks


def _meter_delta_check(code, label, group, svc, usage, unit) -> BillAuditCheck:
    rel = [f"{group}_service.last_read", f"{group}_service.current_read",
           f"{group}_service.multiplier", f"{group}_service.{unit.lower()}_used"]
    if svc is None or svc.last_read is None or svc.current_read is None or svc.multiplier is None or usage is None:
        return _skipped(code, label, group, _HIGH, rel)
    expected = (Decimal(svc.current_read) - Decimal(svc.last_read)) * Decimal(svc.multiplier)
    return _equality(code, label, group, _HIGH, expected=expected, actual=usage,
                     tolerance=METER_TOL, related=rel, fail_on_violation=True, unit=unit)


def _rate_sanity(code, label, group, charges, usage, upper, unit) -> BillAuditCheck:
    rel = [f"{group}_service.total_current_charges", f"{group}_service.{unit.lower()}_used"]
    c, u = _dec(charges), _dec(usage)
    if c is None or u is None or u == 0:
        return _skipped(code, label, group, _LOW, rel)
    rate = (c / u).quantize(Decimal("0.0001"))
    ok = Decimal(0) < rate < Decimal(upper)
    return _check(
        code, label, group, _PASS if ok else _WARN, _LOW,
        expected=f"0 < rate < {upper} per {unit}",
        actual=f"{rate}/{unit}",
        message=("Effective rate is within a sane range."
                 if ok else f"Effective rate ${rate}/{unit} is outside the expected sane range."),
        related=rel,
    )


def _electric_checks(bill: Bill) -> list[BillAuditCheck]:
    g = "electric"
    e = bill.electric_service
    kwh = _dec(e.kwh_used) if e else None
    return [
        _meter_delta_check("electric_meter_delta_matches_kwh",
                           "Electric meter reads match kWh used", g, e, kwh, "kWh"),
        _equality(
            "electric_total_line_items_match_total_current_charges",
            "Electric line items sum to the electric total",
            g, _HIGH,
            expected=(e.total_current_charges if e else None),
            actual=_line_total(bill, "electric"),
            tolerance=MONEY_TOL,
            related=["electric line_items", "electric_service.total_current_charges"],
            fail_on_violation=True,
        ),
        _equality(
            "electric_total_current_charges_matches_account_summary",
            "Electric total matches the account-summary electric total",
            g, _HIGH,
            expected=bill.electric_service_total,
            actual=(e.total_current_charges if e else None),
            tolerance=MONEY_TOL,
            related=["electric_service.total_current_charges", "electric_service_total"],
            fail_on_violation=True,
        ),
        _rate_sanity("electric_effective_rate_reasonable",
                     "Electric effective rate is in a sane range",
                     g, (e.total_current_charges if e else None), kwh, "1", "kWh"),
    ]


def _gas_checks(bill: Bill) -> list[BillAuditCheck]:
    g = "gas"
    gas_svc = bill.gas_service
    ccf = _dec(gas_svc.ccf_used) if gas_svc else None
    return [
        _meter_delta_check("gas_meter_delta_matches_ccf",
                           "Gas meter reads match CCF used", g, gas_svc, ccf, "CCF"),
        _equality(
            "gas_total_line_items_match_total_current_charges",
            "Gas line items sum to the gas total",
            g, _HIGH,
            expected=(gas_svc.total_current_charges if gas_svc else None),
            actual=_line_total(bill, "gas"),
            tolerance=MONEY_TOL,
            related=["gas line_items", "gas_service.total_current_charges"],
            fail_on_violation=True,
        ),
        _equality(
            "gas_total_current_charges_matches_account_summary",
            "Gas total matches the account-summary gas total",
            g, _HIGH,
            expected=bill.gas_service_total,
            actual=(gas_svc.total_current_charges if gas_svc else None),
            tolerance=MONEY_TOL,
            related=["gas_service.total_current_charges", "gas_service_total"],
            fail_on_violation=True,
        ),
        _rate_sanity("gas_effective_rate_reasonable",
                     "Gas effective rate is in a sane range",
                     g, (gas_svc.total_current_charges if gas_svc else None), ccf, "20", "CCF"),
    ]


def _snapshot_checks(bill: Bill) -> list[BillAuditCheck]:
    g = "snapshot"
    days = bill.billing_days
    checks = []
    for service, total, code, label in (
        ("electric", bill.electric_service_total,
         "electric_average_cost_per_day_matches_total_if_available",
         "Electric avg cost/day matches the snapshot value"),
        ("gas", bill.gas_service_total,
         "gas_average_cost_per_day_matches_total_if_available",
         "Gas avg cost/day matches the snapshot value"),
    ):
        snap = _snapshot_value(bill, service)
        rel = [f"{service}_service_total", "billing_days", f"snapshot.{service}_average_cost_per_day_this_month"]
        t = _dec(total)
        if snap is None or t is None or not days:
            checks.append(_skipped(code, label, g, _LOW, rel))
            continue
        computed = (t / Decimal(days)).quantize(Decimal("0.01"))
        checks.append(
            _equality(code, label, g, _LOW, expected=snap, actual=computed,
                      tolerance=SNAPSHOT_TOL, related=rel, fail_on_violation=False)
        )
    return checks


# --------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------


def _overall(checks: list[BillAuditCheck]) -> BillAuditStatus:
    values = {c.status for c in checks}  # strings (use_enum_values)
    if BillAuditStatus.FAIL.value in values:
        return BillAuditStatus.FAIL
    if BillAuditStatus.WARNING.value in values:
        return BillAuditStatus.WARNING
    return BillAuditStatus.PASS


def run_audit(bill: Bill) -> BillAuditResult:
    """Run all checks against a bill (pure; never raises on bad data — emits skips)."""
    checks = (
        _account_summary_checks(bill)
        + _electric_checks(bill)
        + _gas_checks(bill)
        + _snapshot_checks(bill)
    )
    return BillAuditResult(
        bill_id=bill.id,
        audit_version=AUDIT_VERSION,
        overall_status=_overall(checks),
        checks=checks,
        created_at=datetime.now(timezone.utc),
    )


def attach_audit(bill: Bill) -> BillAuditResult:
    """Compute the audit and attach a BillAudit row to the bill (caller commits)."""
    result = run_audit(bill)
    bill.audit = BillAudit(
        audit_version=result.audit_version,
        overall_status=result.overall_status,  # already a str (use_enum_values)
        result_json=result.model_dump(mode="json"),
    )
    return result


def get_or_create_audit(db, bill: Bill) -> dict:
    """Return the stored audit JSON, computing + persisting it on first request."""
    if bill.audit is not None and bill.audit.result_json:
        return bill.audit.result_json
    result = attach_audit(bill)
    db.commit()
    return result.model_dump(mode="json")
