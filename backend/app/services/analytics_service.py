"""Analytics computed over a user's bill history.

All computations run in Python over the ORM rows (DB-agnostic: identical on SQLite and
PostgreSQL). Money is returned as precision-preserving strings; usage as numbers.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, List, Optional

from sqlalchemy.orm import Session

from ..models import Bill
from . import bill_service
from .bill_service import d_str, iso, usage_num

# Anomaly thresholds (per the spec). Usage compared per-day (billing-cycle normalized).
USAGE_ANOMALY_FACTOR = Decimal("1.25")  # +25% above the average of previous bills
RATE_ANOMALY_FACTOR = Decimal("1.15")   # +15% above the historical average
MIN_RELIABLE_HISTORY = 3                 # fewer than this -> "insufficient history"


def _mom(prev: Optional[Decimal], cur: Optional[Decimal]) -> Optional[dict]:
    """Month-over-month change between the two most recent bills."""
    if prev is None or cur is None:
        return None
    prev, cur = Decimal(prev), Decimal(cur)
    absolute = cur - prev
    percent = round(float(absolute / prev * 100), 2) if prev != 0 else None
    return {"from": str(prev), "to": str(cur), "absolute": str(absolute), "percent": percent}


def _avg(values: list[Decimal]) -> Optional[str]:
    vals = [Decimal(v) for v in values if v is not None]
    if not vals:
        return None
    return str((sum(vals) / len(vals)).quantize(Decimal("0.0001")))


def _kwh(bill: Bill) -> Optional[Decimal]:
    return bill.electric_service.kwh_used if bill.electric_service else None


def _ccf(bill: Bill) -> Optional[Decimal]:
    return bill.gas_service.ccf_used if bill.gas_service else None


def _bill_ref(bill: Bill, **extra) -> dict:
    return {"bill_id": bill.id, "bill_date": iso(bill.bill_date), **extra}


def _max_by(bills: list[Bill], key) -> Optional[Bill]:
    candidates = [(key(b), b) for b in bills if key(b) is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda kb: kb[0])[1]


def summary(db: Session) -> dict[str, Any]:
    user = bill_service.get_or_create_default_user(db)
    bills = bill_service.bills_for_user(db, user.id)

    if not bills:
        return {
            "total_bills": 0,
            "latest": None,
            "latest_bill_amount": None,
            "latest_electric_usage_kwh": None,
            "latest_gas_usage_ccf": None,
            "month_over_month": {"total_bill": None, "electric_kwh": None, "gas_ccf": None},
            "average_electric_effective_rate": None,
            "average_gas_effective_rate": None,
            "highest_electric_usage_bill": None,
            "highest_gas_usage_bill": None,
            "highest_total_bill": None,
        }

    latest = bills[-1]
    prev = bills[-2] if len(bills) >= 2 else None

    highest_elec = _max_by(bills, _kwh)
    highest_gas = _max_by(bills, _ccf)
    highest_total = _max_by(bills, lambda b: b.total_amount_due)

    return {
        "total_bills": len(bills),
        "latest": _bill_ref(latest, total_amount_due=d_str(latest.total_amount_due)),
        "latest_bill_amount": d_str(latest.total_amount_due),
        "latest_electric_usage_kwh": usage_num(_kwh(latest)),
        "latest_gas_usage_ccf": usage_num(_ccf(latest)),
        "month_over_month": {
            "total_bill": _mom(prev.total_amount_due if prev else None, latest.total_amount_due),
            "electric_kwh": _mom(_kwh(prev) if prev else None, _kwh(latest)),
            "gas_ccf": _mom(_ccf(prev) if prev else None, _ccf(latest)),
        },
        "average_electric_effective_rate": _avg(
            [b.electric_service.effective_rate_per_kwh for b in bills if b.electric_service]
        ),
        "average_gas_effective_rate": _avg(
            [b.gas_service.effective_rate_per_ccf for b in bills if b.gas_service]
        ),
        "highest_electric_usage_bill": (
            _bill_ref(highest_elec, kwh_used=usage_num(_kwh(highest_elec))) if highest_elec else None
        ),
        "highest_gas_usage_bill": (
            _bill_ref(highest_gas, ccf_used=usage_num(_ccf(highest_gas))) if highest_gas else None
        ),
        "highest_total_bill": (
            _bill_ref(highest_total, total_amount_due=d_str(highest_total.total_amount_due))
            if highest_total
            else None
        ),
    }


def electric(db: Session) -> dict[str, Any]:
    user = bill_service.get_or_create_default_user(db)
    bills = [b for b in bill_service.bills_for_user(db, user.id) if b.electric_service]

    series = [
        {
            "bill_id": b.id,
            "bill_date": iso(b.bill_date),
            "kwh_used": usage_num(b.electric_service.kwh_used),
            "effective_rate_per_kwh": d_str(b.electric_service.effective_rate_per_kwh),
            "average_cost_per_day": d_str(b.electric_service.average_cost_per_day),
            "average_kwh_per_day": d_str(b.electric_service.average_kwh_per_day),
            "total_current_charges": d_str(b.electric_service.total_current_charges),
            "billing_days": b.electric_service.billing_days,
        }
        for b in bills
    ]
    latest = bills[-1] if bills else None
    prev = bills[-2] if len(bills) >= 2 else None
    highest = _max_by(bills, _kwh)
    total_kwh = sum((Decimal(_kwh(b)) for b in bills if _kwh(b) is not None), Decimal(0))

    return {
        "service": "electric",
        "bill_count": len(bills),
        "series": series,
        "latest_usage_kwh": usage_num(_kwh(latest)) if latest else None,
        "month_over_month_kwh": _mom(_kwh(prev) if prev else None, _kwh(latest) if latest else None),
        "average_effective_rate": _avg(
            [b.electric_service.effective_rate_per_kwh for b in bills]
        ),
        "highest_usage_bill": (
            _bill_ref(highest, kwh_used=usage_num(_kwh(highest))) if highest else None
        ),
        "total_kwh": usage_num(total_kwh),
    }


def gas(db: Session) -> dict[str, Any]:
    user = bill_service.get_or_create_default_user(db)
    bills = [b for b in bill_service.bills_for_user(db, user.id) if b.gas_service]

    series = [
        {
            "bill_id": b.id,
            "bill_date": iso(b.bill_date),
            "ccf_used": usage_num(b.gas_service.ccf_used),
            "effective_rate_per_ccf": d_str(b.gas_service.effective_rate_per_ccf),
            "average_cost_per_day": d_str(b.gas_service.average_cost_per_day),
            "average_ccf_per_day": d_str(b.gas_service.average_ccf_per_day),
            "total_current_charges": d_str(b.gas_service.total_current_charges),
            "billing_days": b.gas_service.billing_days,
        }
        for b in bills
    ]
    latest = bills[-1] if bills else None
    prev = bills[-2] if len(bills) >= 2 else None
    highest = _max_by(bills, _ccf)
    total_ccf = sum((Decimal(_ccf(b)) for b in bills if _ccf(b) is not None), Decimal(0))

    return {
        "service": "gas",
        "bill_count": len(bills),
        "series": series,
        "latest_usage_ccf": usage_num(_ccf(latest)) if latest else None,
        "month_over_month_ccf": _mom(_ccf(prev) if prev else None, _ccf(latest) if latest else None),
        "average_effective_rate": _avg([b.gas_service.effective_rate_per_ccf for b in bills]),
        "highest_usage_bill": (
            _bill_ref(highest, ccf_used=usage_num(_ccf(highest))) if highest else None
        ),
        "total_ccf": usage_num(total_ccf),
    }


# ======================================================================================
# Insights service — spec functions + deterministic insight builder
#
# Everything below operates ONLY on already-parsed, structured ORM data. No LLM is used;
# every number is computed deterministically. Wording is cautious — this is a bill
# analytics tool, not an oracle.
# ======================================================================================


def _half_up(value, places: str) -> Optional[Decimal]:
    if value is None:
        return None
    return Decimal(value).quantize(Decimal(places), rounding=ROUND_HALF_UP)


def _per_day(usage, days, places: str = "0.01") -> Optional[Decimal]:
    if usage is None or not days:
        return None
    return _half_up(Decimal(usage) / Decimal(days), places)


def _eff_rate(charges, usage, places: str = "0.0001") -> Optional[Decimal]:
    if charges is None or not usage:
        return None
    u = Decimal(usage)
    if u == 0:
        return None
    return _half_up(Decimal(charges) / u, places)


def _avg_dec(values, places: str = "0.0001") -> Optional[Decimal]:
    vals = [Decimal(v) for v in values if v is not None]
    if not vals:
        return None
    return _half_up(sum(vals) / len(vals), places)


def _pct_change(prev, cur) -> Optional[float]:
    if prev is None or cur is None:
        return None
    prev, cur = Decimal(prev), Decimal(cur)
    if prev == 0:
        return None
    return float(_half_up((cur - prev) / prev * 100, "0.01"))


def _diff(a, b, places: str = "0.01") -> Optional[Decimal]:
    if a is None or b is None:
        return None
    return _half_up(Decimal(a) - Decimal(b), places)


def _money_str(value) -> Optional[str]:
    return None if value is None else f"${Decimal(value):,.2f}"


def _int_str(value) -> Optional[str]:
    return None if value is None else f"{int(Decimal(value)):,}"


def _bills(db: Session, user_id: int) -> List[Bill]:
    return bill_service.bills_for_user(db, user_id)


# ---- spec functions -------------------------------------------------------------------


def get_latest_bill_summary(db: Session, user_id: int) -> Optional[dict]:
    bills = _bills(db, user_id)
    if not bills:
        return None
    b = bills[-1]
    e, g = b.electric_service, b.gas_service
    return {
        "bill_id": b.id,
        "bill_date": iso(b.bill_date),
        "due_date": iso(b.due_date),
        "billing_days": b.billing_days,
        "total_amount_due": d_str(b.total_amount_due),
        "current_charges_total": d_str(b.current_charges_total),
        "electric_service_total": d_str(b.electric_service_total),
        "gas_service_total": d_str(b.gas_service_total),
        "autopay_status": b.autopay_status,
        "kwh_used": usage_num(e.kwh_used) if e else None,
        "ccf_used": usage_num(g.ccf_used) if g else None,
        "avg_kwh_per_day": d_str(_per_day(e.kwh_used, e.billing_days)) if e else None,
        "avg_ccf_per_day": d_str(_per_day(g.ccf_used, g.billing_days)) if g else None,
        "electric_effective_rate": d_str(_eff_rate(e.total_current_charges, e.kwh_used)) if e else None,
        "gas_effective_rate": d_str(_eff_rate(g.total_current_charges, g.ccf_used)) if g else None,
    }


def get_bill_trends(db: Session, user_id: int) -> List[dict]:
    return [
        {
            "bill_id": b.id,
            "bill_date": iso(b.bill_date),
            "total_amount_due": d_str(b.total_amount_due),
            "current_charges_total": d_str(b.current_charges_total),
            "electric_service_total": d_str(b.electric_service_total),
            "gas_service_total": d_str(b.gas_service_total),
            "billing_days": b.billing_days,
        }
        for b in _bills(db, user_id)
    ]


def get_electric_trends(db: Session, user_id: int) -> List[dict]:
    out = []
    for b in _bills(db, user_id):
        e = b.electric_service
        if not e:
            continue
        out.append(
            {
                "bill_id": b.id,
                "bill_date": iso(b.bill_date),
                "kwh_used": usage_num(e.kwh_used),
                "billing_days": e.billing_days,
                "avg_kwh_per_day": d_str(_per_day(e.kwh_used, e.billing_days)),
                "effective_rate_per_kwh": d_str(_eff_rate(e.total_current_charges, e.kwh_used)),
                "total_current_charges": d_str(e.total_current_charges),
                "avg_cost_per_day": d_str(_per_day(e.total_current_charges, e.billing_days)),
            }
        )
    return out


def get_gas_trends(db: Session, user_id: int) -> List[dict]:
    out = []
    for b in _bills(db, user_id):
        g = b.gas_service
        if not g:
            continue
        out.append(
            {
                "bill_id": b.id,
                "bill_date": iso(b.bill_date),
                "ccf_used": usage_num(g.ccf_used),
                "billing_days": g.billing_days,
                "avg_ccf_per_day": d_str(_per_day(g.ccf_used, g.billing_days)),
                "effective_rate_per_ccf": d_str(_eff_rate(g.total_current_charges, g.ccf_used)),
                "total_current_charges": d_str(g.total_current_charges),
                "avg_cost_per_day": d_str(_per_day(g.total_current_charges, g.billing_days)),
            }
        )
    return out


def _derived_metrics(bills: List[Bill]) -> dict:
    """The full set of derived metrics (latest vs previous + rolling averages)."""
    if not bills:
        return {}
    latest = bills[-1]
    prev = bills[-2] if len(bills) >= 2 else None
    le, lg = latest.electric_service, latest.gas_service
    pe = prev.electric_service if prev else None
    pg = prev.gas_service if prev else None

    latest_kwh = le.kwh_used if le else None
    prev_kwh = pe.kwh_used if pe else None
    latest_ccf = lg.ccf_used if lg else None
    prev_ccf = pg.ccf_used if pg else None

    roll_total = _avg_dec([b.total_amount_due for b in bills], "0.01")
    roll_kwh_day = _avg_dec(
        [_per_day(b.electric_service.kwh_used, b.electric_service.billing_days)
         for b in bills if b.electric_service],
        "0.01",
    )
    roll_ccf_day = _avg_dec(
        [_per_day(b.gas_service.ccf_used, b.gas_service.billing_days)
         for b in bills if b.gas_service],
        "0.01",
    )

    return {
        "latest_total_bill": d_str(latest.total_amount_due),
        "previous_total_bill": d_str(prev.total_amount_due) if prev else None,
        "total_bill_change_amount": d_str(
            _diff(latest.total_amount_due, prev.total_amount_due) if prev else None
        ),
        "total_bill_change_percent": _pct_change(
            prev.total_amount_due if prev else None, latest.total_amount_due
        ),
        "latest_kwh": usage_num(latest_kwh),
        "previous_kwh": usage_num(prev_kwh),
        "kwh_change_percent": _pct_change(prev_kwh, latest_kwh),
        "latest_ccf": usage_num(latest_ccf),
        "previous_ccf": usage_num(prev_ccf),
        "ccf_change_percent": _pct_change(prev_ccf, latest_ccf),
        "latest_electric_effective_rate": d_str(_eff_rate(le.total_current_charges, latest_kwh)) if le else None,
        "latest_gas_effective_rate": d_str(_eff_rate(lg.total_current_charges, latest_ccf)) if lg else None,
        "rolling_average_total_bill": d_str(roll_total),
        "rolling_average_kwh_per_day": d_str(roll_kwh_day),
        "rolling_average_ccf_per_day": d_str(roll_ccf_day),
    }


def compare_latest_bill_to_previous(db: Session, user_id: int) -> Optional[dict]:
    bills = _bills(db, user_id)
    if not bills:
        return None
    m = _derived_metrics(bills)
    return {
        "has_previous": len(bills) >= 2,
        "latest_total_bill": m["latest_total_bill"],
        "previous_total_bill": m["previous_total_bill"],
        "total_bill_change_amount": m["total_bill_change_amount"],
        "total_bill_change_percent": m["total_bill_change_percent"],
        "latest_kwh": m["latest_kwh"],
        "previous_kwh": m["previous_kwh"],
        "kwh_change_percent": m["kwh_change_percent"],
        "latest_ccf": m["latest_ccf"],
        "previous_ccf": m["previous_ccf"],
        "ccf_change_percent": m["ccf_change_percent"],
    }


def calculate_effective_rates(db: Session, user_id: int) -> dict:
    bills = _bills(db, user_id)
    if not bills:
        return {
            "latest_electric_effective_rate": None,
            "latest_gas_effective_rate": None,
            "average_electric_effective_rate": None,
            "average_gas_effective_rate": None,
        }
    latest = bills[-1]
    le, lg = latest.electric_service, latest.gas_service
    elec_rates = [
        _eff_rate(b.electric_service.total_current_charges, b.electric_service.kwh_used)
        for b in bills if b.electric_service
    ]
    gas_rates = [
        _eff_rate(b.gas_service.total_current_charges, b.gas_service.ccf_used)
        for b in bills if b.gas_service
    ]
    return {
        "latest_electric_effective_rate": d_str(_eff_rate(le.total_current_charges, le.kwh_used)) if le else None,
        "latest_gas_effective_rate": d_str(_eff_rate(lg.total_current_charges, lg.ccf_used)) if lg else None,
        "average_electric_effective_rate": d_str(_avg_dec(elec_rates)),
        "average_gas_effective_rate": d_str(_avg_dec(gas_rates)),
        "unit_electric": "$/kWh",
        "unit_gas": "$/CCF",
    }


def _flag_usage(latest: Bill, previous: List[Bill], kind: str) -> Optional[dict]:
    """Per-day (billing-cycle normalized) usage anomaly: >25% above the previous average."""
    svc_attr = "electric_service" if kind == "electric" else "gas_service"
    usage_attr = "kwh_used" if kind == "electric" else "ccf_used"
    unit = "kWh" if kind == "electric" else "CCF"

    ls = getattr(latest, svc_attr)
    if not ls or getattr(ls, usage_attr) is None or not ls.billing_days:
        return None
    latest_pd = _per_day(getattr(ls, usage_attr), ls.billing_days)
    prev_pds = [
        _per_day(getattr(s, usage_attr), s.billing_days)
        for b in previous
        if (s := getattr(b, svc_attr)) and getattr(s, usage_attr) is not None and s.billing_days
    ]
    base = _avg_dec(prev_pds, "0.01")
    if not base or base <= 0 or latest_pd is None or latest_pd <= base * USAGE_ANOMALY_FACTOR:
        return None
    pct = _pct_change(base, latest_pd)
    return {
        "type": f"high_{kind}_usage",
        "metric": f"{unit.lower()}_per_day",
        "observed": d_str(latest_pd),
        "baseline_average": d_str(base),
        "threshold_percent": 25,
        "change_percent": pct,
        "severity": "info",
        "message": (
            f"{kind.capitalize()} usage averaged {latest_pd} {unit}/day this cycle — about "
            f"{pct}% above your recent average of {base} {unit}/day."
        ),
    }


def _flag_rate(latest: Bill, previous: List[Bill], kind: str) -> Optional[dict]:
    """Effective-rate anomaly: >15% above the historical average."""
    svc_attr = "electric_service" if kind == "electric" else "gas_service"
    usage_attr = "kwh_used" if kind == "electric" else "ccf_used"
    unit = "kWh" if kind == "electric" else "CCF"

    ls = getattr(latest, svc_attr)
    if not ls or not getattr(ls, usage_attr) or ls.total_current_charges is None:
        return None
    latest_rate = _eff_rate(ls.total_current_charges, getattr(ls, usage_attr))
    prev_rates = [
        _eff_rate(s.total_current_charges, getattr(s, usage_attr))
        for b in previous
        if (s := getattr(b, svc_attr)) and getattr(s, usage_attr) and s.total_current_charges is not None
    ]
    base = _avg_dec(prev_rates, "0.0001")
    if not base or base <= 0 or latest_rate is None or latest_rate <= base * RATE_ANOMALY_FACTOR:
        return None
    pct = _pct_change(base, latest_rate)
    return {
        "type": f"high_{kind}_rate",
        "metric": f"effective_rate_per_{unit.lower()}",
        "observed": d_str(latest_rate),
        "baseline_average": d_str(base),
        "threshold_percent": 15,
        "change_percent": pct,
        "severity": "info",
        "message": (
            f"Effective {kind} rate was approximately ${latest_rate}/{unit} — about {pct}% "
            f"above your historical average of ${base}/{unit}."
        ),
    }


def detect_basic_anomalies(db: Session, user_id: int) -> dict:
    """Apply the basic anomaly rules. Honest about short history."""
    bills = _bills(db, user_id)
    warnings: List[str] = []
    reliable = len(bills) >= MIN_RELIABLE_HISTORY
    if len(bills) < MIN_RELIABLE_HISTORY:
        warnings.append("More history is needed before anomaly detection can be considered reliable.")

    anomalies: List[dict] = []
    if len(bills) >= 2:
        latest, previous = bills[-1], bills[:-1]
        for flag in (
            _flag_usage(latest, previous, "electric"),
            _flag_usage(latest, previous, "gas"),
            _flag_rate(latest, previous, "electric"),
            _flag_rate(latest, previous, "gas"),
        ):
            if flag:
                anomalies.append(flag)

    return {"reliable": reliable, "bill_count": len(bills), "anomalies": anomalies, "warnings": warnings}


def _summary_text(bills: List[Bill], reliable: bool) -> str:
    b = bills[-1]
    e, g = b.electric_service, b.gas_service
    parts = [
        f"Your latest bill is {_money_str(b.total_amount_due)}, with "
        f"{_money_str(b.electric_service_total)} for electric service and "
        f"{_money_str(b.gas_service_total)} for gas service."
    ]
    if e and e.kwh_used is not None and e.billing_days:
        parts.append(
            f"Electric usage was {_int_str(e.kwh_used)} kWh over {e.billing_days} days, "
            f"averaging {_per_day(e.kwh_used, e.billing_days)} kWh/day."
        )
    if g and g.ccf_used is not None and g.billing_days:
        parts.append(
            f"Gas usage was {_int_str(g.ccf_used)} CCF over {g.billing_days} days, "
            f"averaging {_per_day(g.ccf_used, g.billing_days)} CCF/day."
        )
    if e and e.kwh_used:
        parts.append(
            f"Effective electric rate was approximately ${_eff_rate(e.total_current_charges, e.kwh_used, '0.001')}/kWh."
        )
    if g and g.ccf_used:
        parts.append(
            f"Effective gas rate was approximately ${_eff_rate(g.total_current_charges, g.ccf_used, '0.01')}/CCF."
        )
    if not reliable:
        parts.append("More history is needed before anomaly detection can be considered reliable.")
    return " ".join(parts)


def _recommendations(bills: List[Bill], anomalies: List[dict], reliable: bool) -> List[str]:
    recs: List[str] = []
    if len(bills) < MIN_RELIABLE_HISTORY:
        recs.append("Upload more monthly bills to enable reliable trend and anomaly detection.")
    by_type = {a["type"] for a in anomalies}
    if "high_electric_usage" in by_type:
        recs.append(
            "Electric usage per day is notably higher than your recent average — consider "
            "checking heating/cooling settings or any new high-draw appliances."
        )
    if "high_gas_usage" in by_type:
        recs.append(
            "Gas usage per day is notably higher than your recent average — colder weather or "
            "heating use may explain it; otherwise check for unusual consumption."
        )
    if "high_electric_rate" in by_type:
        recs.append(
            "Your effective electric rate is higher than usual — review the rate riders "
            "(PCRF/SCRF) and any tier changes shown on the bill."
        )
    if "high_gas_rate" in by_type:
        recs.append(
            "Your effective gas rate is higher than usual — review the Purchased Gas Factor and "
            "interim rate adjustment shown on the bill."
        )
    if reliable and not anomalies:
        recs.append("No unusual changes detected versus your recent history.")
    return recs


def build_insights(db: Session, user_id: int) -> dict:
    """Assemble the deterministic /api/analytics/insights payload."""
    bills = _bills(db, user_id)
    if not bills:
        return {
            "summary": "No bills have been uploaded yet. Upload a CoServ PDF to see insights.",
            "metrics": {},
            "warnings": ["No bills available yet."],
            "anomalies": [],
            "recommendations": ["Upload a CoServ bill PDF to get started."],
        }
    anom = detect_basic_anomalies(db, user_id)
    return {
        "summary": _summary_text(bills, anom["reliable"]),
        "metrics": _derived_metrics(bills),
        "warnings": list(anom["warnings"]),
        "anomalies": anom["anomalies"],
        "recommendations": _recommendations(bills, anom["anomalies"], anom["reliable"]),
    }
