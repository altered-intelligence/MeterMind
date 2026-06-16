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
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .schemas import CorrectionRequest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .config import get_settings
from .connectors.coserv_portal_connector import get_portal_connector
from .database import get_session, init_db
from .errors import ApiError, DuplicateBillError, ErrorCode, register_error_handlers
from .logging_config import configure_logging
from .parsers import coserv_pdf_parser
from .services import (
    analytics_service,
    assistant_service,
    bill_audit_service,
    bill_service,
    correction_service,
    llm_summary_service,
    ollama_runtime,
    rate_breakdown_service,
)
from .services.correction_service import CorrectionError

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


@app.post("/api/bills/upload", status_code=201)
async def upload_bill(
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
) -> dict:
    filename = file.filename or "upload.pdf"
    content_type = (file.content_type or "").lower()
    if not (filename.lower().endswith(".pdf") or "pdf" in content_type):
        raise ApiError(ErrorCode.INVALID_PDF, "Only PDF files are supported.", 415)

    data = await file.read()
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
        return correction_service.create_correction(
            db,
            bill_id,
            field_path=payload.field_path,
            corrected_value=payload.corrected_value,
            reason=payload.reason,
            user_id=user.id,
        )
    except CorrectionError as exc:
        raise ApiError(ErrorCode.VALIDATION_FAILED, str(exc), exc.status_code)


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
    """Deterministic summary, plus an optional local-LLM (Ollama) summary of redacted
    metrics when ``OLLAMA_ENABLED=true`` and the model is reachable."""
    row = bill_service.get_bill(db, bill_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Bill not found.")
    return llm_summary_service.summarize_bill(db, row)


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


@app.get("/api/analytics/insights")
def analytics_insights(db: Session = Depends(get_session)) -> dict:
    """Deterministic insights: summary, metrics, warnings, anomalies, recommendations."""
    user = bill_service.get_or_create_default_user(db)
    return analytics_service.build_insights(db, user.id)


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


# --- Future CoServ SmartHub connector (placeholder; disabled in this build) ---


@app.get("/api/portal/status")
def portal_status() -> dict:
    """Capability/status of the SmartHub connector. Disabled in the MVP (no automation)."""
    return get_portal_connector().status().model_dump()


@app.post("/api/portal/sync")
def portal_sync() -> dict:
    """Placeholder. Live SmartHub automation is not implemented — always 501 in this build."""
    raise HTTPException(status_code=501, detail=get_portal_connector().status().message)
