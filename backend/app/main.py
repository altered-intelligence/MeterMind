"""FastAPI application for Home Utility Intelligence (CoServ MVP).

Endpoints (all under /api):
    GET    /api/health
    GET    /api/version             app + parser versions
    POST   /api/bills/upload        multipart "file" -> parse, store, return bill
    GET    /api/bills               list all bills (time series, derived analytics)
    GET    /api/bills/{id}          one bill (PII redacted)
    GET    /api/bills/{id}/json     export parsed JSON (PII redacted)
    GET    /api/bills/{id}/debug    redacted extracted-text blocks + warnings
    GET    /api/bills/{id}/audit    deterministic internal-math audit result
    GET    /api/bills/{id}/summary  fast deterministic summary + LLM metadata
    GET    /api/bills/{id}/summary/stream   stream local-LLM summary tokens (text/plain)
    DELETE /api/bills/{id}          delete bill + stored PDF
    GET    /api/analytics/summary   portfolio summary (totals, MoM, averages, peaks)
    GET    /api/analytics/electric  electric time series + aggregates
    GET    /api/analytics/gas       gas time series + aggregates
    GET    /api/analytics/insights  deterministic summary/metrics/warnings/anomalies/recs
    GET    /api/assistant/status    whether local-LLM chat is enabled + data sources
    GET    /api/assistant/models    installed Ollama models + the active one
    POST   /api/assistant/model     switch the active model
    POST   /api/assistant/chat      stream answers (NDJSON: status/think/answer events)
    GET    /api/portal/status       SmartHub connector status (disabled placeholder)
    POST   /api/portal/sync         placeholder -> 501 (no automation in this build)

Privacy: account numbers, names, addresses, and raw PDF text are never logged. Account
and meter numbers are hashed (never stored in full); responses only ever expose the last
4 digits + a redacted address.
"""

from __future__ import annotations

import json
import hmac
import ipaddress
import logging
from contextlib import asynccontextmanager
from datetime import date

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from .schemas import CorrectionRequest, ReprocessRequest, WeatherLocationCreate
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .config import get_settings
from .connectors.coserv_portal_connector import get_portal_connector
from .connectors.coserv_portal import get_connector as get_coserv_connector
from .connectors.coserv_portal.exceptions import PortalConnectorError
from .database import get_session, init_db
from .errors import ApiError, DuplicateBillError, ErrorCode, register_error_handlers
from .logging_config import configure_logging
from .parsers import coserv_pdf_parser
from .services import (
    analytics_service,
    assistant_service,
    audit_log_service,
    bill_audit_service,
    bill_comparison_service,
    bill_service,
    correction_service,
    export_service,
    forecasting_service,
    insights_service,
    investigation_question_service,
    llm_summary_service,
    ollama_runtime,
    parser_service,
    rate_breakdown_service,
    weather_normalization_service,
)
from .services.bill_service import ReprocessError
from .services.correction_service import CorrectionError
from .services.weather import weather_service
from .services.weather.geocoding import GeocodingError
from .services.weather.weather_service import WeatherUnavailable

logger = logging.getLogger("hui")

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.debug_parser)
    init_db()
    # Never log the storage path's contents — only that startup happened.
    logger.info("Home Utility Intelligence backend started.")
    # This MVP has no API authentication by design (local single-user). Warn loudly if it
    # looks like it might be exposed beyond localhost, since that would expose bill data.
    exposed = [
        o for o in settings.cors_origins
        if "localhost" not in o and "127.0.0.1" not in o
    ]
    if exposed:
        logger.warning(
            "API has NO authentication and CORS allows non-localhost origins (%s). "
            "Do not expose this service publicly without an authenticating reverse proxy.",
            ", ".join(exposed),
        )
    yield


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
register_error_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Conservative defaults for a single-user local app. The CSP is written so the built SPA
# works even when served from the same origin as the API: scripts/styles/images/fonts from
# 'self' (Vite emits hashed asset files; 'unsafe-inline' on style-src covers Tailwind's
# injected styles), XHR/fetch limited to 'self', and framing fully locked down.
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    ),
}


@app.middleware("http")
async def enforce_local_access(request, call_next):
    token = settings.api_token
    if token:
        supplied = request.headers.get("authorization", "")
        if len(token) < 32 or not hmac.compare_digest(supplied.encode(), ("Bearer " + token).encode()):
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
    else:
        try:
            local = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
        except ValueError:
            local = False
        if not local:
            return JSONResponse({"detail": "Local access only; configure HUI_API_TOKEN for authenticated access"}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    for key, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(key, value)
    return response


@app.get("/api/privacy/status")
def privacy_status() -> dict:
    """Current privacy mode + which integrations are external (no secrets)."""
    from .security import privacy

    return privacy.integration_status()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name}


@app.get("/api/version")
def version() -> dict:
    return {
        "app": settings.app_name,
        "app_version": settings.app_version,
        "parser_version": coserv_pdf_parser.PARSER_VERSION,
    }


@app.get("/api/parsers")
def list_parsers() -> list[dict]:
    """All registered provider parsers + versions (newest marked ``is_latest``)."""
    return parser_service.list_providers()


@app.get("/api/parsers/{provider}")
def get_provider_parsers(provider: str) -> dict:
    payload = parser_service.get_provider(provider)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"No parser registered for '{provider}'.")
    return payload


@app.get("/api/parsers/{provider}/latest")
def get_provider_latest_parser(provider: str) -> dict:
    payload = parser_service.get_provider_latest(provider)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"No parser registered for '{provider}'.")
    return payload


@app.post("/api/bills/upload", status_code=201)
async def upload_bill(
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
) -> dict:
    filename = file.filename or "upload.pdf"
    content_type = (file.content_type or "").lower()
    if not (filename.lower().endswith(".pdf") or "pdf" in content_type):
        raise ApiError(ErrorCode.INVALID_PDF, "Only PDF files are supported.", 415)

    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if not data:
        raise ApiError(ErrorCode.INVALID_PDF, "Uploaded file is empty.", 400)
    if len(data) > MAX_UPLOAD_BYTES:
        raise ApiError(ErrorCode.INVALID_PDF, "File too large (max 20 MB).", 413)
    if not data[:5].startswith(b"%PDF"):
        raise ApiError(ErrorCode.INVALID_PDF, "File is not a valid PDF.", 415)

    try:
        row = bill_service.ingest_bill(db, file_bytes=data, filename=filename)
    except DuplicateBillError as exc:
        raise ApiError(ErrorCode.DUPLICATE_BILL, str(exc), 409)
    except SQLAlchemyError:
        logger.exception("Database error during ingestion")  # no PDF content / PII
        raise ApiError(ErrorCode.DATABASE_ERROR, "Could not store the bill.", 500)
    except Exception:  # noqa: BLE001 — never surface internals/PII to the client
        logger.exception("Bill ingestion failed")  # stack trace only, no PDF content
        raise ApiError(ErrorCode.PARSER_FAILED, "Could not parse the uploaded bill.", 422)

    audit_log_service.log_event(
        db, audit_log_service.BILL_UPLOADED,
        user_id=row.user_id, object_type="bill", object_id=row.id,
        metadata={"confidence": row.parser_confidence, "warnings": len(row.warnings)},
    )
    return bill_service.serialize_bill(row)


@app.post("/api/bills/bulk-upload", status_code=201)
async def bulk_upload_bills(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_session),
) -> dict:
    """Import many PDFs at once. Each file is processed independently and gets a per-file
    result (imported / warning / duplicate / failed); one bad file never aborts the batch."""
    if not files:
        raise ApiError(ErrorCode.VALIDATION_FAILED, "No files were provided.", 400)

    payload: list[tuple[str, bytes]] = []
    for f in files:
        payload.append((f.filename or "upload.pdf", await f.read()))

    try:
        batch = bill_service.bulk_ingest(db, payload)
    except SQLAlchemyError:
        logger.exception("Database error during bulk ingestion")  # no PDF content / PII
        raise ApiError(ErrorCode.DATABASE_ERROR, "Could not complete the import.", 500)

    return bill_service.serialize_import_batch(batch)


@app.get("/api/import-batches")
def list_import_batches(db: Session = Depends(get_session)) -> list[dict]:
    return [bill_service.serialize_import_batch(b) for b in bill_service.list_import_batches(db)]


@app.get("/api/import-batches/{batch_id}")
def get_import_batch(batch_id: int, db: Session = Depends(get_session)) -> dict:
    batch = bill_service.get_import_batch(db, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Import batch not found.")
    return bill_service.serialize_import_batch(batch)


@app.get("/api/bills")
def list_bills(db: Session = Depends(get_session)) -> list[dict]:
    return [bill_service.serialize_bill(row) for row in bill_service.list_bills(db)]


@app.get("/api/bills/{bill_id}")
def get_bill(bill_id: int, db: Session = Depends(get_session)) -> dict:
    eff = correction_service.get_effective_bill_data(db, bill_id)
    if eff is None:
        raise HTTPException(status_code=404, detail="Bill not found.")
    bill, corrections, corrected_paths = eff
    return correction_service.serialize_effective_bill(bill, corrections, corrected_paths)


@app.get("/api/bills/{bill_id}/corrections")
def list_corrections(bill_id: int, db: Session = Depends(get_session)) -> dict:
    if bill_service.get_bill(db, bill_id) is None:
        raise HTTPException(status_code=404, detail="Bill not found.")
    corrections = correction_service.get_corrections(db, bill_id)
    return {
        "bill_id": bill_id,
        "supported_fields": correction_service.supported_field_paths(),
        "corrections": [correction_service._correction_model(c) for c in corrections],
    }


@app.post("/api/bills/{bill_id}/corrections", status_code=201)
def create_correction(
    bill_id: int,
    payload: CorrectionRequest,
    db: Session = Depends(get_session),
) -> dict:
    """Correct one parsed field. The original parsed value is preserved; the corrected
    value is stored separately and the audit is re-run on the corrected data."""
    if bill_service.get_bill(db, bill_id) is None:
        raise HTTPException(status_code=404, detail="Bill not found.")
    user = bill_service.get_or_create_default_user(db)
    try:
        result = correction_service.create_correction(
            db,
            bill_id,
            field_path=payload.field_path,
            corrected_value=payload.corrected_value,
            reason=payload.reason,
            user_id=user.id,
        )
    except CorrectionError as exc:
        raise ApiError(ErrorCode.VALIDATION_FAILED, str(exc), exc.status_code)
    # Audit the action — field_path only, never the corrected value (may be PII-adjacent).
    audit_log_service.log_event(
        db, audit_log_service.CORRECTION_CREATED,
        user_id=user.id, object_type="bill", object_id=bill_id,
        metadata={"field_path": payload.field_path},
    )
    return result


@app.post("/api/bills/{bill_id}/reprocess")
def reprocess_bill(
    bill_id: int,
    payload: ReprocessRequest | None = None,
    db: Session = Depends(get_session),
) -> dict:
    """Re-run the latest (or a pinned) parser over the bill's stored PDF.

    Updates the bill's current parsed data, records an auditable parser run, re-runs the
    audit, and preserves manual corrections (warning if any no longer line up). The original
    PDF and correction history are never destroyed."""
    target = payload.target_parser_version if payload else None
    audit_log_service.log_event(
        db, audit_log_service.PARSER_REPROCESS_STARTED,
        object_type="bill", object_id=bill_id, metadata={"target_version": target},
    )
    try:
        result = bill_service.reprocess_bill(db, bill_id, target_parser_version=target)
    except ReprocessError as exc:
        raise ApiError(ErrorCode.PARSER_FAILED, str(exc), exc.status_code)
    except SQLAlchemyError:
        logger.exception("Database error during reprocess")  # no PDF content / PII
        raise ApiError(ErrorCode.DATABASE_ERROR, "Could not reprocess the bill.", 500)
    if result is None:
        raise HTTPException(status_code=404, detail="Bill not found.")
    audit_log_service.log_event(
        db, audit_log_service.PARSER_REPROCESS_COMPLETED,
        object_type="bill", object_id=bill_id,
        metadata={"parser_version": result.parser_run.parser_version},
    )
    return result.model_dump(mode="json")


@app.get("/api/bills/{bill_id}/comparison")
def bill_comparison(bill_id: int, db: Session = Depends(get_session)) -> dict:
    """What changed on this bill vs the immediately previous bill for the same account.

    Usage and cost are normalized by billing days. Uses corrected values when present."""
    result = bill_comparison_service.compare_bill(db, bill_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Bill not found.")
    return result.model_dump(mode="json")


@app.get("/api/analytics/latest-comparison")
def latest_comparison(db: Session = Depends(get_session)) -> dict:
    """What changed on the most recent bill vs the one before it (same account)."""
    return bill_comparison_service.latest_comparison(db).model_dump(mode="json")


@app.get("/api/bills/{bill_id}/investigation-questions")
def bill_investigation_questions(bill_id: int, db: Session = Depends(get_session)) -> dict:
    """Deterministic, rules-based questions to investigate for a bill (no LLM)."""
    qs = investigation_question_service.build_questions(db, bill_id)
    if qs is None:
        raise HTTPException(status_code=404, detail="Bill not found.")
    return qs.model_dump(mode="json")


@app.get("/api/bills/{bill_id}/json")
def export_bill_json(bill_id: int, db: Session = Depends(get_session)) -> dict:
    row = bill_service.get_bill(db, bill_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Bill not found.")
    return bill_service.export_json(row)


@app.get("/api/bills/{bill_id}/debug")
def bill_debug(bill_id: int, db: Session = Depends(get_session)) -> dict:
    row = bill_service.get_bill(db, bill_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Bill not found.")
    return bill_service.bill_debug_blocks(row)


@app.get("/api/bills/{bill_id}/audit")
def bill_audit(bill_id: int, db: Session = Depends(get_session)) -> dict:
    """Deterministic internal-math audit (computed on first request if not yet stored)."""
    row = bill_service.get_bill(db, bill_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Bill not found.")
    return bill_audit_service.get_or_create_audit(db, row)


@app.get("/api/bills/{bill_id}/rate-breakdown")
def bill_rate_breakdown(bill_id: int, db: Session = Depends(get_session)) -> dict:
    """Deterministic rate & fee breakdown (fixed vs variable vs taxes/fees/adjustments)."""
    row = bill_service.get_bill(db, bill_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Bill not found.")
    return rate_breakdown_service.breakdown_dict(row)


@app.get("/api/bills/{bill_id}/summary")
def bill_summary(bill_id: int, db: Session = Depends(get_session)) -> dict:
    """Deterministic summary + a structured explanation (5 fields).

    The ``explanation`` is computed deterministically here (fast, non-blocking); when the
    local LLM is enabled the client can call ``/summary/regenerate`` for an LLM-phrased
    version. Every number always comes from the deterministic pipeline."""
    row = bill_service.get_bill(db, bill_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Bill not found.")
    data = llm_summary_service.summarize_bill(db, row)
    data["explanation"] = llm_summary_service.generate_structured_summary(db, row)
    return data


@app.post("/api/bills/{bill_id}/summary/regenerate")
def regenerate_bill_summary(bill_id: int, db: Session = Depends(get_session)) -> dict:
    """(Re)generate the structured summary, using the local LLM when enabled.

    Falls back to the deterministic summary if Ollama is disabled or unavailable — the
    request never fails. The LLM receives only the redacted metrics payload."""
    row = bill_service.get_bill(db, bill_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Bill not found.")
    explanation = llm_summary_service.generate_structured_summary(db, row, force_llm=True)
    if explanation.get("generated_locally"):
        audit_log_service.log_event(
            db, audit_log_service.LOCAL_LLM_SUMMARY_GENERATED,
            user_id=row.user_id, object_type="bill", object_id=bill_id,
            metadata={"model": explanation.get("llm_model")},
        )
    return {"bill_id": bill_id, "explanation": explanation}


@app.get("/api/bills/{bill_id}/summary/stream")
def bill_summary_stream(bill_id: int, db: Session = Depends(get_session)) -> StreamingResponse:
    """Stream the local-LLM summary token-by-token (so a slow model never blocks the UI).

    The redacted payload is built up front, so no DB access happens during streaming.
    """
    row = bill_service.get_bill(db, bill_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Bill not found.")
    if not settings.ollama_enabled:
        raise HTTPException(status_code=409, detail="Local LLM summaries are disabled.")

    payload = llm_summary_service.build_redacted_payload(db, row)

    def gen():
        yield from llm_summary_service.stream_llm_summary(
            payload,
            model=ollama_runtime.get_active_model(),
            host=settings.ollama_host,
            timeout=settings.ollama_timeout_seconds,
        )

    return StreamingResponse(
        gen(),
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.delete("/api/bills/{bill_id}", status_code=204)
def delete_bill(bill_id: int, db: Session = Depends(get_session)) -> None:
    if not bill_service.delete_bill(db, bill_id):
        raise HTTPException(status_code=404, detail="Bill not found.")
    audit_log_service.log_event(
        db, audit_log_service.BILL_DELETED, object_type="bill", object_id=bill_id
    )


@app.get("/api/analytics/summary")
def analytics_summary(db: Session = Depends(get_session)) -> dict:
    return analytics_service.summary(db)


@app.get("/api/analytics/electric")
def analytics_electric(db: Session = Depends(get_session)) -> dict:
    return analytics_service.electric(db)


@app.get("/api/analytics/gas")
def analytics_gas(db: Session = Depends(get_session)) -> dict:
    return analytics_service.gas(db)


@app.get("/api/analytics/rate-breakdown-trends")
def analytics_rate_breakdown_trends(db: Session = Depends(get_session)) -> list[dict]:
    """Per-bill effective-rate trend points (chronological) for the trend charts."""
    bills = bill_service.list_bills(db)  # already sorted chronologically
    return rate_breakdown_service.trends(bills)


@app.get("/api/insights/latest")
def insights_latest(db: Session = Depends(get_session)) -> dict:
    """Unified latest-bill insights aggregated from existing services (partial on failure)."""
    return insights_service.latest_insights(db)


@app.get("/api/analytics/insights")
def analytics_insights(db: Session = Depends(get_session)) -> dict:
    """Deterministic insights: summary, metrics, warnings, anomalies, recommendations."""
    user = bill_service.get_or_create_default_user(db)
    return analytics_service.build_insights(db, user.id)


# --- Weather context + normalization ---


@app.post("/api/weather/location", status_code=201)
def set_weather_location(
    payload: WeatherLocationCreate, db: Session = Depends(get_session)
) -> dict:
    """Configure the weather location from a ZIP code or explicit lat/lon. Replaces any
    existing one."""
    user = bill_service.get_or_create_default_user(db)
    latitude, longitude = payload.latitude, payload.longitude
    label = payload.label
    timezone_name = payload.timezone or "America/Chicago"

    # Geocode the ZIP when coordinates weren't supplied directly.
    if (latitude is None or longitude is None) and payload.zip_code:
        try:
            res = weather_service.resolve_zip(payload.zip_code, payload.country)
        except GeocodingError as exc:
            raise ApiError(ErrorCode.VALIDATION_FAILED, str(exc), exc.status_code)
        latitude, longitude = res.latitude, res.longitude
        label = label or res.label
        if not payload.timezone and res.timezone:
            timezone_name = res.timezone

    try:
        loc = weather_service.set_location(
            db, user,
            latitude=latitude, longitude=longitude,
            label=label, timezone_name=timezone_name,
        )
    except WeatherUnavailable as exc:
        raise ApiError(ErrorCode.VALIDATION_FAILED, str(exc), exc.status_code)
    return weather_service.serialize_location(loc)


@app.get("/api/weather/location")
def get_weather_location(db: Session = Depends(get_session)) -> dict | None:
    """The configured weather location, or ``null`` if none is set yet."""
    user = bill_service.get_or_create_default_user(db)
    loc = weather_service.get_location(db, user)
    return weather_service.serialize_location(loc) if loc else None


@app.post("/api/bills/{bill_id}/weather/sync", status_code=201)
def sync_bill_weather(bill_id: int, db: Session = Depends(get_session)) -> dict:
    """Fetch/cache weather over the bill's service period and (re)build its summary."""
    bill = bill_service.get_bill(db, bill_id)
    if bill is None:
        raise HTTPException(status_code=404, detail="Bill not found.")
    user = bill_service.get_or_create_default_user(db)
    loc = weather_service.get_location(db, user)
    if loc is None:
        raise ApiError(
            ErrorCode.VALIDATION_FAILED,
            "No weather location configured. Set one via POST /api/weather/location first.",
            409,
        )
    audit_log_service.log_event(
        db, audit_log_service.WEATHER_SYNC_STARTED,
        user_id=user.id, object_type="bill", object_id=bill_id,
    )
    try:
        summary = weather_service.sync_bill_weather(db, bill, loc)
    except WeatherUnavailable as exc:
        raise ApiError(ErrorCode.VALIDATION_FAILED, str(exc), exc.status_code)
    except Exception:  # noqa: BLE001 — provider/network failure, never leak internals/PII
        logger.exception("weather sync failed for bill %s", bill_id)
        raise ApiError(ErrorCode.PARSER_FAILED, "Could not fetch weather for this bill.", 502)
    return {
        "summary": weather_service.serialize_summary(summary),
        "normalization": weather_normalization_service.normalize_bill(db, bill_id).model_dump(mode="json"),
    }


@app.get("/api/bills/{bill_id}/weather")
def get_bill_weather(bill_id: int, db: Session = Depends(get_session)) -> dict:
    """The bill's stored weather summary + weather-normalized usage context."""
    norm = weather_normalization_service.normalize_bill(db, bill_id)
    if norm is None:
        raise HTTPException(status_code=404, detail="Bill not found.")
    summary = weather_service.get_bill_summary(db, bill_id)
    return {
        "summary": weather_service.serialize_summary(summary) if summary else None,
        "normalization": norm.model_dump(mode="json"),
    }


@app.get("/api/analytics/weather-normalized")
def analytics_weather_normalized(db: Session = Depends(get_session)) -> dict:
    """Per-bill weather-normalized points (kWh/day vs CDD, CCF/day vs HDD) for charts."""
    return weather_normalization_service.normalized_series(db).model_dump(mode="json")


# --- Reporting & export ---


def _attachment(content, media_type: str, filename: str) -> Response:
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _audit_export(db: Session, fmt: str, *, include_sensitive: bool = False, bill_id: int | None = None) -> None:
    audit_log_service.log_event(
        db, audit_log_service.BILL_EXPORTED,
        object_type="bill" if bill_id else "export", object_id=bill_id,
        metadata={"format": fmt, "include_sensitive": export_service.sensitive_allowed(include_sensitive)},
    )


@app.get("/api/export/bills.json")
def export_bills_json(include_sensitive: bool = False, db: Session = Depends(get_session)) -> Response:
    data = export_service.bills_json(db, include_sensitive=include_sensitive)
    _audit_export(db, "bills.json", include_sensitive=include_sensitive)
    return _attachment(json.dumps(data, indent=2), "application/json", "bills.json")


@app.get("/api/export/bills.csv")
def export_bills_csv(db: Session = Depends(get_session)) -> Response:
    content = export_service.bills_csv(db)
    _audit_export(db, "bills.csv")
    return _attachment(content, "text/csv", "bills.csv")


@app.get("/api/export/electric.csv")
def export_electric_csv(db: Session = Depends(get_session)) -> Response:
    content = export_service.electric_csv(db)
    _audit_export(db, "electric.csv")
    return _attachment(content, "text/csv", "electric.csv")


@app.get("/api/export/gas.csv")
def export_gas_csv(db: Session = Depends(get_session)) -> Response:
    content = export_service.gas_csv(db)
    _audit_export(db, "gas.csv")
    return _attachment(content, "text/csv", "gas.csv")


@app.get("/api/export/charges.csv")
def export_charges_csv(db: Session = Depends(get_session)) -> Response:
    content = export_service.charges_csv(db)
    _audit_export(db, "charges.csv")
    return _attachment(content, "text/csv", "charges.csv")


@app.get("/api/bills/{bill_id}/export.json")
def export_one_bill_json(
    bill_id: int, include_sensitive: bool = False, db: Session = Depends(get_session)
) -> Response:
    data = export_service.bill_json(db, bill_id, include_sensitive=include_sensitive)
    if data is None:
        raise HTTPException(status_code=404, detail="Bill not found.")
    _audit_export(db, "bill.json", include_sensitive=include_sensitive, bill_id=bill_id)
    return _attachment(json.dumps(data, indent=2), "application/json", f"bill-{bill_id}.json")


@app.get("/api/bills/{bill_id}/report.pdf")
def export_bill_report_pdf(
    bill_id: int, include_sensitive: bool = False, db: Session = Depends(get_session)
) -> Response:
    pdf = export_service.bill_report_pdf(db, bill_id, include_sensitive=include_sensitive)
    if pdf is None:
        raise HTTPException(status_code=404, detail="Bill not found.")
    _audit_export(db, "report.pdf", include_sensitive=include_sensitive, bill_id=bill_id)
    return _attachment(pdf, "application/pdf", f"bill-{bill_id}-report.pdf")


@app.get("/api/security/audit-events")
def list_audit_events(limit: int = 200, db: Session = Depends(get_session)) -> list[dict]:
    """Recent security audit events (newest first). Contains no sensitive values."""
    limit = max(1, min(limit, 1000))  # clamp: reject 0/negative, cap runaway requests
    return [audit_log_service.serialize_event(e) for e in audit_log_service.list_events(db, limit=limit)]


# --- Forecasting (historical projection — no ML, no live usage) ---


@app.get("/api/forecast/next-bill")
def forecast_next_bill(
    utility_account_id: int | None = None, db: Session = Depends(get_session)
) -> dict:
    """Project the next bill using the historical-average cycle length."""
    return forecasting_service.next_bill_forecast(
        db, utility_account_id=utility_account_id
    ).model_dump(mode="json")


@app.get("/api/forecast")
def forecast(
    expected_billing_days: int | None = None,
    forecast_start_date: date | None = None,
    forecast_end_date: date | None = None,
    utility_account_id: int | None = None,
    db: Session = Depends(get_session),
) -> dict:
    """Project a bill for an expected cycle length (or explicit date range)."""
    return forecasting_service.forecast(
        db,
        expected_billing_days=expected_billing_days,
        forecast_start_date=forecast_start_date,
        forecast_end_date=forecast_end_date,
        utility_account_id=utility_account_id,
    ).model_dump(mode="json")


# --- Assistant chat (optional local LLM over ALL the user's structured data) ---


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


class ModelSelect(BaseModel):
    model: str


@app.get("/api/assistant/status")
def assistant_status() -> dict:
    return {
        "enabled": settings.ollama_enabled,
        "model": ollama_runtime.get_active_model() if settings.ollama_enabled else None,
        "sources": assistant_service.registered_sources(),
    }


@app.get("/api/assistant/models")
def assistant_models() -> dict:
    """Models installed in the local Ollama, plus the currently-active one."""
    available = ollama_runtime.list_installed_models()
    active = ollama_runtime.get_active_model()
    # Show the active model as the canonical installed tag so the UI selector matches.
    if available:
        active = ollama_runtime.resolve_model(active, available) or active
    return {"enabled": settings.ollama_enabled, "active": active, "available": available}


@app.post("/api/assistant/model")
def assistant_set_model(req: ModelSelect) -> dict:
    """Switch the active model (treats 'x' and 'x:latest' as the same installed model)."""
    name = req.model.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Model name is required.")
    available = ollama_runtime.list_installed_models()
    canonical = ollama_runtime.resolve_model(name, available) if available else name
    if available and canonical is None:
        raise HTTPException(
            status_code=400,
            detail=f"'{name}' is not installed. Available: {', '.join(available)}",
        )
    ollama_runtime.set_active_model(canonical or name)
    return {"active": ollama_runtime.get_active_model()}


@app.post("/api/assistant/chat")
def assistant_chat(req: ChatRequest, db: Session = Depends(get_session)) -> StreamingResponse:
    """Stream an answer about ALL the user's redacted data, as NDJSON events.

    Each line is a JSON object {"t": "status"|"think"|"answer", "d": "<text>"}: 'think'
    surfaces the model's reasoning (for models that support it) so the user can see
    progress; 'answer' is the final reply.
    """
    if not settings.ollama_enabled:
        raise HTTPException(status_code=409, detail="Local LLM chat is disabled (set OLLAMA_ENABLED=true).")
    if not req.messages:
        raise HTTPException(status_code=400, detail="No message provided.")

    user = bill_service.get_or_create_default_user(db)
    context = assistant_service.build_context(db, user.id)  # built before streaming (no DB use mid-stream)
    conversation = [{"role": m.role, "content": m.content} for m in req.messages[-20:]]
    messages = assistant_service.build_messages(context, conversation)
    model = ollama_runtime.get_active_model()
    think = llm_summary_service.supports_thinking(model, settings.ollama_host)

    def gen():
        yield json.dumps({"t": "status", "d": f"Querying {model}…"}) + "\n"
        produced = False
        for event in llm_summary_service.stream_ollama_events(
            messages,
            model=model,
            host=settings.ollama_host,
            timeout=settings.ollama_timeout_seconds,
            think=think,
        ):
            produced = True
            yield json.dumps(event) + "\n"
        if not produced:
            yield json.dumps({"t": "status", "d": "No response from the local model."}) + "\n"

    return StreamingResponse(
        gen(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- CoServ SmartHub portal connector (architecture/stubs; no automation) ---


@app.get("/api/connectors/coserv/status")
def coserv_connector_status() -> dict:
    """Connector capability/status. Always safe to call (no automation runs)."""
    return get_coserv_connector().status().model_dump(mode="json")


@app.post("/api/connectors/coserv/start-login")
def coserv_connector_start_login(db: Session = Depends(get_session)) -> dict:
    """Placeholder. Returns 501 — disabled (with the manual-upload message) or planned."""
    connector = get_coserv_connector()
    user = bill_service.get_or_create_default_user(db)
    try:
        return connector.start_interactive_login(user.id).model_dump(mode="json")
    except PortalConnectorError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@app.post("/api/connectors/coserv/disconnect")
def coserv_connector_disconnect(db: Session = Depends(get_session)) -> dict:
    """Safe no-op logout/disconnect (there is never an active session in this build)."""
    user = bill_service.get_or_create_default_user(db)
    get_coserv_connector().logout(user.id)
    return {"status": "disconnected"}


# --- Legacy SmartHub placeholder endpoints (kept for back-compatibility) ---


@app.get("/api/portal/status")
def portal_status() -> dict:
    """Capability/status of the SmartHub connector. Disabled in the MVP (no automation)."""
    return get_portal_connector().status().model_dump()


@app.post("/api/portal/sync")
def portal_sync() -> dict:
    """Placeholder. Live SmartHub automation is not implemented — always 501 in this build."""
    raise HTTPException(status_code=501, detail=get_portal_connector().status().message)
