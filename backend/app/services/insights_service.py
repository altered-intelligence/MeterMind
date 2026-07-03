"""Unified "latest bill" insights aggregator.

Calls the existing services and stitches their outputs into one payload for the Insights
page — it owns **no** analytics logic of its own. Each section is gathered defensively: if a
service raises, that section is ``null`` and a warning is recorded, so the endpoint always
returns partial insights rather than failing. A ``data_quality`` block summarizes coverage.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from . import bill_service

logger = logging.getLogger("hui.insights")


def _safe(section: str, fn: Callable[[], Any], warnings: list[str]) -> Optional[Any]:
    """Run ``fn`` for ``section``; on failure record a warning and return ``None``."""
    try:
        return fn()
    except Exception:  # noqa: BLE001 - one bad section must not sink the whole page
        logger.warning("insights: section '%s' failed; returning partial.", section)
        warnings.append(f"The '{section}' section could not be generated.")
        return None


def latest_insights(db: Session) -> dict:
    """Aggregate everything about the latest bill from the existing services."""
    warnings: list[str] = []

    bills = bill_service.list_bills(db)  # corrected, chronological
    if not bills:
        return {
            "status": "no_bills",
            "bill": None, "comparison": None, "audit": None, "rate_breakdown": None,
            "anomalies": None, "weather": None, "forecast": None, "questions": [],
            "parser": None, "warnings": ["No bills yet — upload a CoServ bill to get started."],
            "data_quality": {"status": "no_data", "has_history": False, "sections_available": []},
        }

    latest = bills[-1]
    bill_id = latest.id

    # Import services lazily to avoid import cycles at module load.
    from . import (
        analytics_service,
        bill_audit_service,
        bill_comparison_service,
        correction_service,
        forecasting_service,
        investigation_question_service,
        rate_breakdown_service,
        weather_normalization_service,
    )

    user = bill_service.get_or_create_default_user(db)

    bill = _safe("bill", lambda: _effective_bill_json(db, bill_id, correction_service), warnings)

    comparison = _safe(
        "comparison",
        lambda: bill_comparison_service.compare_bill(db, bill_id).model_dump(mode="json"),
        warnings,
    )
    # One session-attached, eager-loaded bill shared by audit + rate breakdown (each
    # get_bill() is a full multi-relationship query — no need to run it twice).
    attached = bill_service.get_bill(db, bill_id)
    audit = _safe(
        "audit", lambda: bill_audit_service.get_or_create_audit(db, attached), warnings
    )
    rate_breakdown = _safe(
        "rate_breakdown", lambda: rate_breakdown_service.breakdown_dict(attached), warnings
    )
    anomalies = _safe(
        "anomalies", lambda: analytics_service.detect_basic_anomalies(db, user.id), warnings
    )
    def _weather():
        norm = weather_normalization_service.normalize_bill(db, bill_id)
        return norm.model_dump(mode="json") if norm is not None else None

    weather = _safe("weather", _weather, warnings)
    forecast = _safe(
        "forecast", lambda: forecasting_service.next_bill_forecast(db).model_dump(mode="json"), warnings
    )
    questions = _safe(
        "questions",
        lambda: (investigation_question_service.build_questions(db, bill_id).model_dump(mode="json")
                 or {}).get("questions", []),
        warnings,
    ) or []
    parser = _safe("parser", lambda: _parser_meta(bill), warnings)

    # Data-quality summary.
    sections = {
        "bill": bill, "comparison": comparison, "audit": audit, "rate_breakdown": rate_breakdown,
        "anomalies": anomalies, "weather": weather, "forecast": forecast,
    }
    available = [k for k, v in sections.items() if v is not None]
    has_history = len(bills) > 1
    weather_on = weather is not None and (weather or {}).get("status") == "ok"
    forecast_on = forecast is not None and (forecast or {}).get("status") == "ok"

    data_quality = {
        "status": "degraded" if warnings else "ok",
        "bill_count": len(bills),
        "has_history": has_history,
        "weather_available": weather_on,
        "forecast_available": forecast_on,
        "parser_confidence": (parser or {}).get("parser_confidence"),
        "audit_status": (audit or {}).get("overall_status"),
        "sections_available": available,
    }

    return {
        "status": "ok",
        "bill": bill,
        "comparison": comparison,
        "audit": audit,
        "rate_breakdown": rate_breakdown,
        "anomalies": anomalies,
        "weather": weather,
        "forecast": forecast,
        "questions": questions,
        "parser": parser,
        "warnings": warnings,
        "data_quality": data_quality,
    }


def _effective_bill_json(db: Session, bill_id: int, correction_service) -> Optional[dict]:
    eff = correction_service.get_effective_bill_data(db, bill_id)
    if eff is None:
        return None
    bill, corrections, corrected_paths = eff
    return correction_service.serialize_effective_bill(bill, corrections, corrected_paths)


def _parser_meta(bill_json: Optional[dict]) -> Optional[dict]:
    if not bill_json:
        return None
    return {
        "parser_version": bill_json.get("parser_version"),
        "parser_schema_version": bill_json.get("parser_schema_version"),
        "parser_confidence": bill_json.get("parser_confidence"),
        "parsed_at": bill_json.get("parsed_at"),
        "warnings": bill_json.get("warnings", []),
        "parser_runs": bill_json.get("parser_runs", []),
    }
