"""Weather-normalized usage analytics.

Puts a bill's electric/gas usage in the context of how hot or cold the billing period was:

* ``kwh_per_cooling_degree_day``      — electric kWh per cooling degree-day (CDD)
* ``gas_ccf_per_heating_degree_day``  — gas CCF per heating degree-day (HDD)

Strict rules (per spec):
  * Never divide by zero — if total CDD (or HDD) is zero, the per-degree-day rate is ``None``.
  * If weather data is missing, the result is ``status="unavailable"``.
  * Uses **corrected** (effective) bill usage when corrections exist.
  * Descriptive context only — this module makes **no causal claims**. Phrasing is neutral
    ("this period had N cooling degree-days; usage was X"), never "usage was high *because*…".
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from ..models import Bill, BillWeatherSummary
from ..schemas import (
    WeatherNormalization,
    WeatherNormalizedAnalytics,
    WeatherNormalizedPoint,
)
from . import bill_service, correction_service
from .weather import weather_service

# Below this many degree-days, a per-degree-day rate is too noisy to characterize, so we
# describe the period without computing a rate (avoids implying precision/causality).
_MIN_DD_FOR_RATE = 1.0


def _dec(v) -> Optional[Decimal]:
    if v is None:
        return None
    return v if isinstance(v, Decimal) else Decimal(str(v))


def _rate(usage: Optional[Decimal], degree_days: Optional[float], places: str = "0.0001") -> Optional[Decimal]:
    """usage / degree_days, or ``None`` when undefined (missing or zero degree-days)."""
    if usage is None or degree_days is None or degree_days < _MIN_DD_FOR_RATE:
        return None
    return (usage / Decimal(str(degree_days))).quantize(Decimal(places))


def _num(v: Optional[Decimal]) -> str:
    if v is None:
        return "—"
    return f"{v:,.0f}" if v == v.to_integral_value() else f"{v:,.2f}"


def _electric_context(kwh: Optional[Decimal], cdd: Optional[float], rate: Optional[Decimal]) -> str:
    if kwh is None or cdd is None:
        return "Not enough data to put electric usage in weather context."
    if cdd < _MIN_DD_FOR_RATE:
        return (
            f"There were effectively no cooling degree-days this period, so electric usage "
            f"({_num(kwh)} kWh) is not attributable to cooling."
        )
    return (
        f"This period had {cdd:,.0f} cooling degree-days. Electric usage was {_num(kwh)} kWh "
        f"— about {rate} kWh per cooling degree-day."
    )


def _gas_context(ccf: Optional[Decimal], hdd: Optional[float], rate: Optional[Decimal]) -> str:
    if ccf is None or hdd is None:
        return "Not enough data to put gas usage in weather context."
    if hdd < _MIN_DD_FOR_RATE:
        return (
            f"There were effectively no heating degree-days this period, so gas usage "
            f"({_num(ccf)} CCF) is not attributable to heating."
        )
    return (
        f"This period had {hdd:,.0f} heating degree-days. Gas usage was {_num(ccf)} CCF "
        f"— about {rate} CCF per heating degree-day."
    )


def _normalize(bill: Bill, summary: BillWeatherSummary) -> WeatherNormalization:
    e, g = bill.electric_service, bill.gas_service
    kwh = _dec(e.kwh_used) if e else None
    kwh_per_day = _dec(e.average_kwh_per_day) if e else None
    ccf = _dec(g.ccf_used) if g else None
    ccf_per_day = _dec(g.average_ccf_per_day) if g else None

    cdd = summary.total_cooling_degree_days_65
    hdd = summary.total_heating_degree_days_65

    kwh_per_cdd = _rate(kwh, cdd, "0.01")
    ccf_per_hdd = _rate(ccf, hdd, "0.0001")

    return WeatherNormalization(
        status="ok",
        bill_id=bill.id,
        kwh_used=kwh,
        kwh_per_day=kwh_per_day,
        total_cooling_degree_days_65=cdd,
        kwh_per_cooling_degree_day=kwh_per_cdd,
        electric_usage_vs_cdd_context=_electric_context(kwh, cdd, kwh_per_cdd),
        ccf_used=ccf,
        ccf_per_day=ccf_per_day,
        total_heating_degree_days_65=hdd,
        gas_ccf_per_heating_degree_day=ccf_per_hdd,
        gas_usage_vs_hdd_context=_gas_context(ccf, hdd, ccf_per_hdd),
    )


def normalize_bill(db: Session, bill_id: int) -> Optional[WeatherNormalization]:
    """Weather-normalized context for one bill, using corrected usage values.

    Returns ``None`` if the bill doesn't exist; ``status="unavailable"`` if the bill has no
    weather summary yet (sync weather first)."""
    eff = correction_service.get_effective_bill_data(db, bill_id)
    if eff is None:
        return None
    bill, _corrections, _paths = eff
    summary = weather_service.get_bill_summary(db, bill_id)
    if summary is None or not summary.days_observed:
        return WeatherNormalization(
            status="unavailable",
            bill_id=bill_id,
            message="No weather summary for this bill yet — sync weather to enable normalization.",
        )
    return _normalize(bill, summary)


def normalized_series(db: Session) -> WeatherNormalizedAnalytics:
    """Per-bill weather-normalized points (only bills that have a weather summary)."""
    user = bill_service.get_or_create_default_user(db)
    bills = bill_service.bills_for_user(db, user.id, apply_corrections=True)
    points: list[WeatherNormalizedPoint] = []
    for bill in bills:
        summary = weather_service.get_bill_summary(db, bill.id)
        if summary is None or not summary.days_observed:
            continue
        norm = _normalize(bill, summary)
        points.append(
            WeatherNormalizedPoint(
                bill_id=bill.id,
                bill_date=bill.bill_date.isoformat() if bill.bill_date else None,
                avg_temp_f=summary.avg_temp_f,
                kwh_per_day=norm.kwh_per_day,
                cooling_degree_days=norm.total_cooling_degree_days_65,
                kwh_per_cooling_degree_day=norm.kwh_per_cooling_degree_day,
                ccf_per_day=norm.ccf_per_day,
                heating_degree_days=norm.total_heating_degree_days_65,
                gas_ccf_per_heating_degree_day=norm.gas_ccf_per_heating_degree_day,
            )
        )
    if not points:
        return WeatherNormalizedAnalytics(
            status="unavailable",
            message="No bills have weather data yet — configure a location and sync weather.",
        )
    return WeatherNormalizedAnalytics(status="ok", points=points)
