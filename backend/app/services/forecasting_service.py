"""Basic bill forecasting — historical projection only (deterministic, no ML).

Projects an upcoming (or current-cycle) bill from past CoServ bills using simple rolling
averages of per-day cost and usage, scaled to the expected number of billing days. It does
**not** use live/real-time usage and says so in its assumptions.

Projection methods (all plain arithmetic):
  1. next_bill_baseline_forecast   — rolling average of recent bills' total amount due.
  2. normalized_daily_cost_forecast — avg total cost/day × expected billing days  (the
     primary projected total, since it adjusts for cycle length).
  3. electric_usage_forecast        — avg kWh/day × expected billing days.
  4. gas_usage_forecast             — avg CCF/day × expected billing days.
  5. weather_context_forecast       — only when weather summaries exist; reports prior-
     average degree-days as context and raises uncertainty when recent weather is atypical.
     Correlation context only — never a causal/ML claim.

Confidence is transparent and rule-based (see ``_confidence``). Uses corrected (effective)
bill values.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from ..models import Bill
from ..schemas import (
    BillForecast,
    ForecastComponent,
    ForecastConfidence,
)
from . import bill_service
from .weather import weather_service

logger = logging.getLogger("hui.forecast")

# How many of the most-recent bills feed the rolling averages.
ROLLING_WINDOW = 6
DEFAULT_BILLING_DAYS = 30
# Recent degree-days this far from the window average flag extra weather uncertainty.
_WEATHER_DEVIATION = Decimal("0.25")

_CONF_LADDER = [
    ForecastConfidence.LOW,
    ForecastConfidence.MEDIUM_LOW,
    ForecastConfidence.MEDIUM,
    ForecastConfidence.HIGH,
]


def _dec(v) -> Optional[Decimal]:
    if v is None:
        return None
    return v if isinstance(v, Decimal) else Decimal(str(v))


def _mean(values: list[Decimal], places: str = "0.0001") -> Optional[Decimal]:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return (sum(vals) / Decimal(len(vals))).quantize(Decimal(places))


def _q(v: Optional[Decimal], places: str) -> Optional[Decimal]:
    return None if v is None else v.quantize(Decimal(places))


def _per_day(total: Optional[Decimal], days: Optional[int]) -> Optional[Decimal]:
    total = _dec(total)
    if total is None or not days:
        return None
    return total / Decimal(days)


# --------------------------------------------------------------------------------------
# Confidence
# --------------------------------------------------------------------------------------


def _confidence(
    n_bills: int, *, has_weather: bool, has_parser_warnings: bool
) -> tuple[ForecastConfidence, list[str]]:
    """Transparent, rule-based confidence (see module docstring / spec)."""
    if n_bills >= 12:
        base = ForecastConfidence.HIGH
    elif n_bills >= 6:
        base = ForecastConfidence.MEDIUM
    elif n_bills >= 3:
        base = ForecastConfidence.MEDIUM_LOW
    else:
        base = ForecastConfidence.LOW

    idx = _CONF_LADDER.index(base)
    notes: list[str] = []

    # 12+ bills only reaches "high" when there's weather context to corroborate it — we
    # don't model rate changes, so without weather we cap at medium.
    if base == ForecastConfidence.HIGH and not has_weather:
        idx = _CONF_LADDER.index(ForecastConfidence.MEDIUM)
        notes.append(
            "Capped at medium: no weather context is available to corroborate a "
            "high-confidence projection (and rate changes aren't modeled)."
        )

    # Parser warnings on the latest bill knock confidence down one level.
    if has_parser_warnings and idx > 0:
        idx -= 1
        notes.append("Lowered one level: the most recent bill has parser warnings.")

    return _CONF_LADDER[idx], notes


# --------------------------------------------------------------------------------------
# Expected billing days
# --------------------------------------------------------------------------------------


def _resolve_expected_days(
    window: list[Bill],
    *,
    expected_billing_days: Optional[int],
    start: Optional[date],
    end: Optional[date],
) -> tuple[int, Optional[str]]:
    """Return (expected_days, note). Explicit dates win, then an override, then the
    historical average, then the 30-day default."""
    if start is not None and end is not None:
        days = (end - start).days + 1
        if days > 0:
            return days, f"Derived {days} days from the requested forecast period."
    if expected_billing_days and expected_billing_days > 0:
        return expected_billing_days, f"Using the requested {expected_billing_days}-day cycle."
    hist = [b.billing_days for b in window if b.billing_days]
    if hist:
        avg = round(sum(hist) / len(hist))
        return avg, f"Using the historical average cycle length ({avg} days)."
    return DEFAULT_BILLING_DAYS, f"Using the default {DEFAULT_BILLING_DAYS}-day cycle (no history)."


# --------------------------------------------------------------------------------------
# Weather context
# --------------------------------------------------------------------------------------


def _weather_context(db: Session, window: list[Bill]) -> tuple[bool, Optional[str], list[ForecastComponent], list[str]]:
    """Return (has_weather, context_text, components, warnings)."""
    summaries = []
    for b in window:
        s = weather_service.get_bill_summary(db, b.id)
        if s is not None and s.days_observed:
            summaries.append(s)
    if not summaries:
        return False, None, [], []

    avg_cdd = _mean([_dec(s.total_cooling_degree_days_65) for s in summaries], "0.1")
    avg_hdd = _mean([_dec(s.total_heating_degree_days_65) for s in summaries], "0.1")
    latest = summaries[-1]
    warnings: list[str] = []

    # If the most recent period's degree-days deviate sharply from the window average, the
    # near-term forecast carries more weather uncertainty (context, not causation).
    def _deviates(latest_v, avg_v) -> bool:
        latest_v, avg_v = _dec(latest_v), avg_v
        if latest_v is None or avg_v is None or avg_v == 0:
            return False
        return abs(latest_v - avg_v) / avg_v > _WEATHER_DEVIATION

    if _deviates(latest.total_cooling_degree_days_65, avg_cdd) or _deviates(
        latest.total_heating_degree_days_65, avg_hdd
    ):
        warnings.append(
            "Recent weather differed notably from the period average, so usage-driven "
            "costs may swing more than this projection assumes."
        )

    text = (
        f"Across recent bills, periods averaged {avg_cdd} cooling and {avg_hdd} heating "
        "degree-days. This projection assumes typical weather; a hotter or colder upcoming "
        "cycle would push usage (and uncertainty) higher."
    )
    comp = ForecastComponent(
        key="weather_context_forecast",
        label="Weather context",
        method="avg degree-days over recent bills (context only, not ML)",
        unit="degree-days",
        inputs={
            "avg_cooling_degree_days": str(avg_cdd) if avg_cdd is not None else None,
            "avg_heating_degree_days": str(avg_hdd) if avg_hdd is not None else None,
            "bills_with_weather": len(summaries),
        },
        note=text,
    )
    return True, text, [comp], warnings


# --------------------------------------------------------------------------------------
# Forecast
# --------------------------------------------------------------------------------------


def _insufficient(account_id: Optional[int]) -> BillForecast:
    return BillForecast(
        status="insufficient_history",
        utility_account_id=account_id,
        confidence=ForecastConfidence.LOW,
        message="Not enough bill history to forecast yet — upload at least one bill.",
        assumptions=["Forecasting projects from past bills; it does not use live usage data."],
    )


def forecast(
    db: Session,
    *,
    expected_billing_days: Optional[int] = None,
    forecast_start_date: Optional[date] = None,
    forecast_end_date: Optional[date] = None,
    utility_account_id: Optional[int] = None,
) -> BillForecast:
    """Project the next/current bill from historical bills (corrected values)."""
    user = bill_service.get_or_create_default_user(db)
    bills = bill_service.bills_for_user(db, user.id, apply_corrections=True)
    if utility_account_id is not None:
        bills = [b for b in bills if b.utility_account_id == utility_account_id]
    if not bills:
        return _insufficient(utility_account_id)

    account_id = utility_account_id or bills[-1].utility_account_id
    n_bills = len(bills)
    window = bills[-ROLLING_WINDOW:]

    # Per-day rolling averages (skip bills lacking the needed inputs / billing days).
    avg_cost_per_day = _mean([_per_day(b.total_amount_due, b.billing_days) for b in window], "0.01")
    avg_elec_per_day = _mean(
        [_per_day(b.electric_service_total, b.billing_days) for b in window], "0.01"
    )
    avg_gas_per_day = _mean([_per_day(b.gas_service_total, b.billing_days) for b in window], "0.01")

    def _kwh_day(b: Bill) -> Optional[Decimal]:
        e = b.electric_service
        if e is None:
            return None
        return _dec(e.average_kwh_per_day) or _per_day(e.kwh_used, b.billing_days)

    def _ccf_day(b: Bill) -> Optional[Decimal]:
        g = b.gas_service
        if g is None:
            return None
        return _dec(g.average_ccf_per_day) or _per_day(g.ccf_used, b.billing_days)

    avg_kwh_per_day = _mean([_kwh_day(b) for b in window], "0.01")
    avg_ccf_per_day = _mean([_ccf_day(b) for b in window], "0.01")

    days, days_note = _resolve_expected_days(
        window,
        expected_billing_days=expected_billing_days,
        start=forecast_start_date,
        end=forecast_end_date,
    )
    days_dec = Decimal(days)

    # Projections.
    proj_total_daily = _q(avg_cost_per_day * days_dec, "0.01") if avg_cost_per_day is not None else None
    proj_electric = _q(avg_elec_per_day * days_dec, "0.01") if avg_elec_per_day is not None else None
    proj_gas = _q(avg_gas_per_day * days_dec, "0.01") if avg_gas_per_day is not None else None
    proj_kwh = _q(avg_kwh_per_day * days_dec, "0.1") if avg_kwh_per_day is not None else None
    proj_ccf = _q(avg_ccf_per_day * days_dec, "0.1") if avg_ccf_per_day is not None else None

    # Baseline = rolling average of recent totals (cycle-length agnostic).
    baseline = _mean([_dec(b.total_amount_due) for b in window], "0.01")

    has_parser_warnings = bool(bills[-1].warnings)
    has_weather, weather_text, weather_components, weather_warnings = _weather_context(db, window)
    confidence, conf_notes = _confidence(
        n_bills, has_weather=has_weather, has_parser_warnings=has_parser_warnings
    )

    components = [
        ForecastComponent(
            key="next_bill_baseline_forecast",
            label="Baseline (rolling average of recent totals)",
            method=f"mean(total_amount_due) over last {len(window)} bills",
            value=baseline,
            unit="USD",
            inputs={"bills_used": len(window)},
        ),
        ForecastComponent(
            key="normalized_daily_cost_forecast",
            label="Normalized daily cost × expected days",
            method="avg total cost/day × expected billing days",
            value=proj_total_daily,
            unit="USD",
            inputs={"avg_cost_per_day": str(avg_cost_per_day) if avg_cost_per_day is not None else None,
                    "expected_billing_days": days},
        ),
        ForecastComponent(
            key="electric_usage_forecast",
            label="Electric usage",
            method="avg kWh/day × expected billing days",
            value=proj_kwh,
            unit="kWh",
            inputs={"avg_kwh_per_day": str(avg_kwh_per_day) if avg_kwh_per_day is not None else None,
                    "expected_billing_days": days},
        ),
        ForecastComponent(
            key="gas_usage_forecast",
            label="Gas usage",
            method="avg CCF/day × expected billing days",
            value=proj_ccf,
            unit="CCF",
            inputs={"avg_ccf_per_day": str(avg_ccf_per_day) if avg_ccf_per_day is not None else None,
                    "expected_billing_days": days},
        ),
    ] + weather_components

    # Primary projected total: the day-normalized estimate (falls back to baseline).
    projected_total = proj_total_daily if proj_total_daily is not None else baseline

    assumptions = [
        "Projected from your past bills only — this is not a live/real-time usage estimate.",
        days_note,
        f"Rolling averages use your {len(window)} most recent bill(s).",
        "Assumes rates and typical usage patterns continue unchanged.",
    ]
    if not has_weather:
        assumptions.append("No weather data was available to weather-adjust the projection.")

    warnings = list(conf_notes) + list(weather_warnings)
    if has_parser_warnings:
        warnings.append("The most recent bill has parser warnings; underlying figures may be imperfect.")

    explanation = (
        f"Projected total of about {_fmt_money(projected_total)} for a {days}-day cycle, "
        f"based on an average of {_fmt_money(avg_cost_per_day)}/day across your "
        f"{len(window)} most recent bill(s)."
    )

    return BillForecast(
        status="ok",
        utility_account_id=account_id,
        bills_analyzed=n_bills,
        rolling_window=len(window),
        expected_billing_days=days,
        forecast_start_date=forecast_start_date.isoformat() if forecast_start_date else None,
        forecast_end_date=forecast_end_date.isoformat() if forecast_end_date else None,
        projected_total_bill=projected_total,
        projected_electric_total=proj_electric,
        projected_gas_total=proj_gas,
        projected_kwh=proj_kwh,
        projected_ccf=proj_ccf,
        projected_average_cost_per_day=avg_cost_per_day,
        confidence=confidence,
        components=components,
        explanation=explanation,
        assumptions=assumptions,
        warnings=warnings,
        weather_context=weather_text,
    )


def _fmt_money(v: Optional[Decimal]) -> str:
    if v is None:
        return "—"
    return f"${v.quantize(Decimal('0.01')):,.2f}"


def next_bill_forecast(db: Session, *, utility_account_id: Optional[int] = None) -> BillForecast:
    """Forecast the next bill using the historical-average cycle length."""
    return forecast(db, utility_account_id=utility_account_id)
