"""Security audit log — append-only record of security-relevant actions.

Records WHAT happened (event type + object reference) and non-sensitive context only.
Sensitive values (account/meter numbers, addresses, names, raw text, barcodes) are NEVER
written here — a guard strips any such keys from metadata defensively.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import SecurityAuditEvent

logger = logging.getLogger("hui.audit")

# Canonical event types.
BILL_UPLOADED = "bill_uploaded"
BILL_DELETED = "bill_deleted"
BILL_EXPORTED = "bill_exported"
CORRECTION_CREATED = "correction_created"
PARSER_REPROCESS_STARTED = "parser_reprocess_started"
PARSER_REPROCESS_COMPLETED = "parser_reprocess_completed"
WEATHER_SYNC_STARTED = "weather_sync_started"
LOCAL_LLM_SUMMARY_GENERATED = "local_llm_summary_generated"

# Metadata keys that must never be persisted (defense in depth against accidental leakage).
_FORBIDDEN_META_KEYS = {
    "account_number", "account", "meter_number", "meter", "name", "customer_name",
    "address", "service_address", "barcode", "payment_barcode", "raw_text", "pdf_text",
    "password", "cookie", "cookies", "token", "secret",
}


def _safe_metadata(metadata: Optional[dict]) -> dict:
    if not metadata:
        return {}
    return {k: v for k, v in metadata.items() if k.lower() not in _FORBIDDEN_META_KEYS}


def log_event(
    db: Session,
    event_type: str,
    *,
    user_id: Optional[int] = None,
    object_type: Optional[str] = None,
    object_id: Optional[int] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    """Append one audit event. Best-effort: never raises into the caller's flow."""
    try:
        event = SecurityAuditEvent(
            user_id=user_id,
            event_type=event_type,
            object_type=object_type,
            object_id=object_id,
            metadata_json=_safe_metadata(metadata),
        )
        db.add(event)
        db.commit()
    except Exception:  # pragma: no cover - auditing must never break the request
        db.rollback()
        logger.warning("failed to write audit event %s (non-fatal)", event_type)


def list_events(db: Session, *, limit: int = 200) -> list[SecurityAuditEvent]:
    return list(
        db.scalars(
            select(SecurityAuditEvent)
            .order_by(SecurityAuditEvent.id.desc())
            .limit(limit)
        ).all()
    )


def serialize_event(e: SecurityAuditEvent) -> dict:
    return {
        "id": e.id,
        "user_id": e.user_id,
        "event_type": e.event_type,
        "object_type": e.object_type,
        "object_id": e.object_id,
        "created_at": e.created_at.isoformat() if e.created_at else None,
        "metadata": e.metadata_json or {},
    }
