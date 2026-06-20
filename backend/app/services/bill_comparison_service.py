"""Month-over-month bill comparison ("what changed vs the previous bill").

For a given bill, this explains what changed relative to the *immediately previous* bill
for the **same utility account**, normalizing usage and cost by billing days so a longer or
shorter cycle doesn't masquerade as a usage change.

Design rules (from the feature spec):

* Compare the bill to the immediately previous one for the same utility account. When there
  is no previous bill, return ``status="insufficient_history"``.
* Always prefer per-day comparisons for usage and cost (``normalized=True``).
* Use **corrected** bill data when corrections exist (effective values).
* Money and rates are :class:`~decimal.Decimal`.
* This service is descriptive only — it never uses anomaly language ("high", "abnormal",
  "spike", ...). Flagging is anomaly detection's job, not this one's.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from ..models import Bill
from ..schemas import (
    BillComparison,
    ChangeDirection,
    ComparisonMetric,
    ComparisonNarrative,
)
from . import bill_service
from ..money import safe_div as _safe_div  # shared Decimal division guard

_PCT = Decimal("0.1")


# --------------------------------------------------------------------------------------
# Value extraction (from a corrected/effective bill)
# --------------------------------------------------------------------------------------


def _dec(v) -> Optional[Decimal]:
    if v is None:
        return None
    return v if isinstance(v, Decimal) else Decimal(str(v))


def _bill_values(bill: Bill) -> dict[str, Optional[Decimal]]:
    """Pull every comparable metric off a (corrected) bill as Decimals."""
    e = bill.electric_service
    g = bill.gas_service
    days = _dec(bill.billing_days)

    total_due = _dec(bill.total_amount_due)
    current_charges = _dec(bill.current_charges_total)
    electric_total = _dec(bill.electric_service_total)
    gas_total = _dec(bill.gas_service_total)

    return {
        # Account
        "total_amount_due": total_due,
        "current_charges_total": current_charges,
        "billing_days": days,
        "average_total_cost_per_day": _safe_div(total_due, days, "0.01"),
        # Electric
        "electric_service_total": electric_total,
        "kwh_used": _dec(e.kwh_used) if e else None,
        "kwh_per_day": _dec(e.average_kwh_per_day) if e else None,
        "electric_cost_per_day": _dec(e.average_cost_per_day) if e else None,
        "effective_electric_rate": _dec(e.effective_rate_per_kwh) if e else None,
        "electric_percentage_of_bill": _safe_div(electric_total, current_charges, "0.0001"),
        # Gas
        "gas_service_total": gas_total,
        "ccf_used": _dec(g.ccf_used) if g else None,
        "ccf_per_day": _dec(g.average_ccf_per_day) if g else None,
        "gas_cost_per_day": _dec(g.average_cost_per_day) if g else None,
        "effective_gas_rate": _dec(g.effective_rate_per_ccf) if g else None,
        "gas_percentage_of_bill": _safe_div(gas_total, current_charges, "0.0001"),
    }


# (key, label, group, unit, normalized-by-billing-days)
_METRIC_DEFS: list[tuple[str, str, str, str, bool]] = [
    # Account
    ("total_amount_due", "Total amount due", "account", "USD", False),
    ("current_charges_total", "Current charges", "account", "USD", False),
    ("billing_days", "Billing days", "account", "days", False),
    ("average_total_cost_per_day", "Average total cost per day", "account", "USD/day", True),
    # Electric
    ("electric_service_total", "Electric service total", "electric", "USD", False),
    ("kwh_used", "Electric usage", "electric", "kWh", False),
    ("kwh_per_day", "Electric usage per day", "electric", "kWh/day", True),
    ("electric_cost_per_day", "Electric cost per day", "electric", "USD/day", True),
    ("effective_electric_rate", "Effective electric rate", "electric", "USD/kWh", False),
    ("electric_percentage_of_bill", "Electric share of bill", "electric", "fraction", False),
    # Gas
    ("gas_service_total", "Gas service total", "gas", "USD", False),
    ("ccf_used", "Gas usage", "gas", "CCF", False),
    ("ccf_per_day", "Gas usage per day", "gas", "CCF/day", True),
    ("gas_cost_per_day", "Gas cost per day", "gas", "USD/day", True),
    ("effective_gas_rate", "Effective gas rate", "gas", "USD/CCF", False),
    ("gas_percentage_of_bill", "Gas share of bill", "gas", "fraction", False),
]


def _build_metric(
    key: str, label: str, group: str, unit: str, normalized: bool, cur, prev
) -> ComparisonMetric:
    if cur is None or prev is None:
        return ComparisonMetric(
            key=key, label=label, group=group, current_value=cur, previous_value=prev,
            absolute_change=None, percent_change=None,
            direction=ChangeDirection.UNAVAILABLE, normalized=normalized, unit=unit,
        )

    absolute = cur - prev
    if prev != 0:
        percent = (absolute / prev * Decimal(100)).quantize(_PCT)
    else:
        percent = None  # division undefined; direction still derived from the absolute change

    if cur > prev:
        direction = ChangeDirection.INCREASED
    elif cur < prev:
        direction = ChangeDirection.DECREASED
    else:
        direction = ChangeDirection.UNCHANGED

    return ComparisonMetric(
        key=key, label=label, group=group, current_value=cur, previous_value=prev,
        absolute_change=absolute, percent_change=percent,
        direction=direction, normalized=normalized, unit=unit,
    )


# --------------------------------------------------------------------------------------
# Narrative (deterministic, anomaly-free)
# --------------------------------------------------------------------------------------


def _money(v: Optional[Decimal]) -> str:
    if v is None:
        return "—"
    q = v.quantize(Decimal("0.01"))
    sign = "-" if q < 0 else ""
    return f"{sign}${abs(q):,.2f}"


def _by_key(metrics: list[ComparisonMetric]) -> dict[str, ComparisonMetric]:
    return {m.key: m for m in metrics}


def _verb(direction: str) -> str:
    return {"increased": "increased", "decreased": "decreased"}.get(direction, "was unchanged")


def _pct_phrase(m: ComparisonMetric) -> Optional[str]:
    """'increased by 8.2% on a per-day basis' / 'was unchanged' / None if unavailable."""
    if m.direction == ChangeDirection.UNAVAILABLE.value:
        return None
    if m.direction == ChangeDirection.UNCHANGED.value:
        return "was unchanged on a per-day basis"
    if m.percent_change is None:
        return f"{_verb(m.direction)} on a per-day basis"
    magnitude = abs(m.percent_change)
    return f"{_verb(m.direction)} by {magnitude}% on a per-day basis"


def _build_narrative(
    metrics: list[ComparisonMetric],
    *,
    cur_days: Optional[int],
    prev_days: Optional[int],
    days_changed: bool,
) -> ComparisonNarrative:
    by = _by_key(metrics)
    lines: list[str] = []

    total = by.get("total_amount_due")
    if total and total.direction != ChangeDirection.UNAVAILABLE.value:
        if total.direction == ChangeDirection.UNCHANGED.value:
            headline = f"Total amount due was unchanged at {_money(total.current_value)}."
        else:
            headline = (
                f"Total amount due {_verb(total.direction)} by "
                f"{_money(abs(total.absolute_change))}, from "
                f"{_money(total.previous_value)} to {_money(total.current_value)}."
            )
    else:
        headline = "Compared this bill to the previous bill."
    lines.append(headline)

    # Billing-period note (so per-cycle totals are read in context).
    if days_changed and cur_days is not None and prev_days is not None:
        verb = "increased" if cur_days > prev_days else "decreased"
        lines.append(
            f"The billing period {verb} from {prev_days} days to {cur_days} days, "
            "so total charges should be interpreted carefully."
        )

    # Prefer per-day usage statements.
    e = by.get("kwh_per_day")
    if e:
        phrase = _pct_phrase(e)
        if phrase:
            lines.append(f"Electric usage {phrase}.")
    g = by.get("ccf_per_day")
    if g:
        phrase = _pct_phrase(g)
        if phrase:
            lines.append(f"Gas usage {phrase}.")

    return ComparisonNarrative(headline=headline, lines=lines)


# --------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------


def _previous_bill(bills: list[Bill], current: Bill) -> Optional[Bill]:
    """The bill immediately before ``current`` (chronological) for the SAME utility account.

    ``bills`` must be chronologically sorted (oldest first), as returned by
    ``bill_service.bills_for_user``. The account match treats a missing account id as its own
    group so unlinked bills only compare to other unlinked bills."""
    acct = current.utility_account_id
    same_account = [b for b in bills if b.utility_account_id == acct]
    prev: Optional[Bill] = None
    for b in same_account:
        if b.id == current.id:
            return prev
        prev = b
    return None


def _insufficient(bill_id: Optional[int]) -> BillComparison:
    return BillComparison(
        status="insufficient_history",
        bill_id=bill_id,
        message="No previous bill for this account yet — upload another bill to see what changed.",
    )


def _compare(current: Bill, previous: Bill) -> BillComparison:
    cur_vals = _bill_values(current)
    prev_vals = _bill_values(previous)

    metrics = [
        _build_metric(key, label, group, unit, normalized, cur_vals[key], prev_vals[key])
        for (key, label, group, unit, normalized) in _METRIC_DEFS
    ]

    cur_days = current.billing_days
    prev_days = previous.billing_days
    days_changed = (
        cur_days is not None and prev_days is not None and cur_days != prev_days
    )

    narrative = _build_narrative(
        metrics, cur_days=cur_days, prev_days=prev_days, days_changed=days_changed
    )

    return BillComparison(
        status="ok",
        bill_id=current.id,
        previous_bill_id=previous.id,
        current_bill_date=current.bill_date.isoformat() if current.bill_date else None,
        previous_bill_date=previous.bill_date.isoformat() if previous.bill_date else None,
        current_billing_days=cur_days,
        previous_billing_days=prev_days,
        billing_days_changed=days_changed,
        metrics=metrics,
        narrative=narrative,
    )


def compare_bill(db: Session, bill_id: int) -> Optional[BillComparison]:
    """Compare ``bill_id`` to the immediately previous bill for the same account.

    Returns ``None`` if the bill doesn't exist, an ``insufficient_history`` result if there
    is no previous bill, else the full comparison. Uses corrected (effective) values."""
    user = bill_service.get_or_create_default_user(db)
    bills = bill_service.bills_for_user(db, user.id, apply_corrections=True)
    current = next((b for b in bills if b.id == bill_id), None)
    if current is None:
        return None
    previous = _previous_bill(bills, current)
    if previous is None:
        return _insufficient(bill_id)
    return _compare(current, previous)


def latest_comparison(db: Session) -> BillComparison:
    """Compare the most recent bill to the one before it (same account)."""
    user = bill_service.get_or_create_default_user(db)
    bills = bill_service.bills_for_user(db, user.id, apply_corrections=True)
    if not bills:
        return _insufficient(None)
    current = bills[-1]
    previous = _previous_bill(bills, current)
    if previous is None:
        return _insufficient(current.id)
    return _compare(current, previous)
