"""Deterministic "questions to investigate" engine (no LLM).

For a given bill it applies a fixed set of explainable rules over the parsed data, the
month-over-month comparison, the deterministic audit, basic anomalies, and (when present)
weather context. Each rule produces a neutral, non-alarmist question plus *why it matters*.

The engine never invents explanations — questions about EV/HVAC/irrigation are explicitly
marked as future-integration only. Output is fully deterministic given the same data.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from ..schemas import (
    BillComparison,
    ComparisonMetric,
    InvestigationQuestion,
    InvestigationQuestionSet,
)
from . import bill_service

logger = logging.getLogger("hui.investigation")

# Thresholds (percentage points).
_BILLING_DAYS_PCT = Decimal("10")
_USAGE_PCT = Decimal("20")
_RATE_PCT = Decimal("10")
# Above this average temperature, the period is "warm" (space heating unlikely).
_WARM_TEMP_F = 68.0

# Future integrations that don't exist yet (so "not integrated" is always true today).
_FUTURE_INTEGRATIONS = ("tesla", "ecobee", "rainbird")


def _metric(comparison: Optional[BillComparison], key: str) -> Optional[ComparisonMetric]:
    if comparison is None:
        return None
    return next((m for m in comparison.metrics if m.key == key), None)


def _increased_by(m: Optional[ComparisonMetric], pct: Decimal) -> bool:
    """True if metric increased by more than ``pct`` percentage points."""
    if m is None or m.direction != "increased" or m.percent_change is None:
        return False
    return m.percent_change > pct


def _changed_by(m: Optional[ComparisonMetric], pct: Decimal) -> bool:
    if m is None or m.percent_change is None:
        return False
    return abs(m.percent_change) > pct


def _q(**kw) -> InvestigationQuestion:
    return InvestigationQuestion(**kw)


def build_questions(db: Session, bill_id: int) -> Optional[InvestigationQuestionSet]:
    """Return the deterministic question set for a bill, or ``None`` if it doesn't exist."""
    from . import (
        analytics_service,
        bill_audit_service,
        bill_comparison_service,
        correction_service,
    )
    from .weather import weather_service

    eff = correction_service.get_effective_bill_data(db, bill_id)
    if eff is None:
        return None
    bill, _corrections, _paths = eff

    # Gather context (each defensively; a missing piece just suppresses its rules).
    try:
        comparison = bill_comparison_service.compare_bill(db, bill_id)
        if comparison.status != "ok":
            comparison = None
    except Exception:  # pragma: no cover
        comparison = None
    try:
        audit_status = str(bill_audit_service.run_audit(bill).overall_status)
    except Exception:  # pragma: no cover
        audit_status = None
    try:
        anom = analytics_service.detect_basic_anomalies(db, bill.user_id)
        anomalies = anom.get("anomalies") or []
    except Exception:  # pragma: no cover
        anomalies = []

    cur_weather = weather_service.get_bill_summary(db, bill_id)
    prev_weather = None
    if comparison is not None and comparison.previous_bill_id is not None:
        prev_weather = weather_service.get_bill_summary(db, comparison.previous_bill_id)

    electric_anomaly = any(
        "kwh" in (str(a.get("metric", "")) + str(a.get("type", ""))).lower()
        or "electric" in (str(a.get("metric", "")) + str(a.get("type", ""))).lower()
        for a in anomalies
    )

    questions: list[InvestigationQuestion] = []

    # --- Parser / data quality --------------------------------------------------------
    if (bill.parser_confidence or "").lower() == "low":
        questions.append(_q(
            question_id="parser_low_confidence", category="parser", severity="medium",
            question="Should this bill be manually reviewed for parser accuracy?",
            why_it_matters="Low parser confidence can affect analytics and comparisons.",
            related_metrics=["parser_confidence"],
        ))
    if audit_status == "fail":
        questions.append(_q(
            question_id="audit_failed", category="parser", severity="high",
            question="Do the bill totals need manual verification?",
            why_it_matters="One or more internal bill math checks failed.",
            related_metrics=["audit_status"],
        ))

    # --- Billing ----------------------------------------------------------------------
    if _changed_by(_metric(comparison, "billing_days"), _BILLING_DAYS_PCT):
        questions.append(_q(
            question_id="billing_days_changed", category="billing", severity="low",
            question="Is the bill different because the billing period length changed?",
            why_it_matters="Longer or shorter billing periods can make total charges look "
                           "unusually high or low.",
            related_metrics=["billing_days", "total_amount_due"],
        ))

    # --- Electric ---------------------------------------------------------------------
    kwh_day = _metric(comparison, "kwh_per_day")
    if _increased_by(kwh_day, _USAGE_PCT):
        questions.append(_q(
            question_id="electric_usage_up", category="electric", severity="medium",
            question="What changed in electric usage during this billing period?",
            why_it_matters="Electric usage increased on a normalized per-day basis.",
            related_metrics=["kwh_per_day"],
        ))
    if _increased_by(_metric(comparison, "effective_electric_rate"), _RATE_PCT):
        questions.append(_q(
            question_id="electric_rate_up", category="electric", severity="medium",
            question="Did rate components or fees change this month?",
            why_it_matters="The effective rate increased beyond raw usage.",
            related_metrics=["effective_electric_rate"],
        ))

    # --- Gas --------------------------------------------------------------------------
    ccf_day = _metric(comparison, "ccf_per_day")
    if _increased_by(ccf_day, _USAGE_PCT):
        questions.append(_q(
            question_id="gas_usage_up", category="gas", severity="medium",
            question="What changed in gas usage during this billing period?",
            why_it_matters="Gas usage increased on a normalized per-day basis.",
            related_metrics=["ccf_per_day"],
        ))
    gas_used = bill.gas_service.ccf_used if bill.gas_service else None
    if (
        cur_weather is not None and cur_weather.avg_temp_f is not None
        and cur_weather.avg_temp_f >= _WARM_TEMP_F
        and gas_used is not None and gas_used > 0
    ):
        questions.append(_q(
            question_id="gas_warm_weather", category="gas", severity="low",
            question="Could gas usage be driven by water heating, cooking, pool heating, or "
                     "another appliance?",
            why_it_matters="Warm-weather gas usage is less likely to be space heating.",
            related_metrics=["ccf_used", "avg_temp_f"],
        ))

    # --- Weather ----------------------------------------------------------------------
    cdd_increased = (
        cur_weather is not None and prev_weather is not None
        and cur_weather.total_cooling_degree_days_65 is not None
        and prev_weather.total_cooling_degree_days_65 is not None
        and cur_weather.total_cooling_degree_days_65 > prev_weather.total_cooling_degree_days_65
    )
    electric_up = _increased_by(kwh_day, Decimal("0"))  # any per-day increase
    if cur_weather is not None and cdd_increased:
        questions.append(_q(
            question_id="weather_cdd_up", category="weather", severity="info",
            question="Did higher cooling demand explain the electric increase?",
            why_it_matters="Cooling degree days increased during this billing period.",
            related_metrics=["cooling_degree_days", "kwh_per_day"],
        ))
    if electric_up and cur_weather is not None and prev_weather is not None and not cdd_increased:
        questions.append(_q(
            question_id="electric_up_weather_flat", category="weather", severity="medium",
            question="Could non-weather electric loads explain the increase?",
            why_it_matters="Weather does not appear to fully explain the electric usage increase.",
            related_metrics=["kwh_per_day", "cooling_degree_days"],
        ))

    # --- Future integrations (clearly future-only) ------------------------------------
    if electric_anomaly:
        questions.append(_q(
            question_id="future_tesla", category="future_integration", severity="info",
            question="Would Tesla charging data help separate EV load from household load?",
            why_it_matters="An electric anomaly was detected; EV charging is a common driver "
                           "that bill data alone can't isolate.",
            related_metrics=["kwh_per_day"],
            required_future_integration="tesla",
            status="needs_future_integration",
        ))
        questions.append(_q(
            question_id="future_ecobee", category="future_integration", severity="info",
            question="Would thermostat runtime help explain HVAC-related usage?",
            why_it_matters="An electric anomaly was detected; HVAC runtime would help attribute it.",
            related_metrics=["kwh_per_day"],
            required_future_integration="ecobee",
            status="needs_future_integration",
        ))
    # Irrigation context is always future-only (no such integration exists yet).
    if bill.electric_service is not None or bill.gas_service is not None:
        questions.append(_q(
            question_id="future_rainbird", category="future_integration", severity="info",
            question="Would irrigation data help explain outdoor resource usage later?",
            why_it_matters="Outdoor/irrigation loads aren't visible in utility-bill data alone.",
            related_metrics=[],
            required_future_integration="rainbird",
            status="needs_future_integration",
        ))

    return InvestigationQuestionSet(bill_id=bill_id, questions=questions)
