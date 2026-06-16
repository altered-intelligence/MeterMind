"""Optional local-LLM bill summaries via Ollama.

Hard rules (enforced here):
  * The LLM **never parses** a bill and **never sees** raw PDF text or PII. It receives
    only a small, redacted, already-validated metrics payload (``build_redacted_payload``):
    bill date, totals, usage, per-day averages, effective rates, and parser warning codes.
    No account number, name, address, meter number, or payment data is ever sent.
  * Ollama is used **only** when ``OLLAMA_ENABLED=true``. If it is disabled or unavailable
    (not running, model missing, timeout), callers fall back to the deterministic summary.
  * Every number shown to the user comes from the deterministic pipeline; the LLM only
    rephrases the provided numbers.

The local model (llama3.1 / mistral / qwen / ...) is configurable via ``OLLAMA_MODEL``.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from functools import lru_cache
from typing import Optional, Tuple

from ..config import get_settings
from ..models import Bill
from . import bill_service, ollama_runtime
from .analytics_service import _avg_dec, _eff_rate, _int_str, _money_str, _pct_change, _per_day
from .bill_service import iso

logger = logging.getLogger("hui.llm")

# Verbatim system prompt from the spec.
SYSTEM_PROMPT = (
    "You are summarizing a utility bill using only the structured data provided. "
    "Do not infer unavailable information. Do not mention personal information. "
    "Be concise, practical, and numerically accurate."
)


def _f(value) -> Optional[float]:
    """Decimal/None -> JSON-safe float (clean shortest repr)."""
    return None if value is None else float(Decimal(str(value)))


def _service_metrics(svc, kind: str) -> Optional[dict]:
    """Redacted per-service metrics (no PII). ``kind`` is 'electric' or 'gas'."""
    if not svc:
        return None
    if kind == "electric":
        usage, usage_key, avg_key = svc.kwh_used, "kwh", "average_kwh_per_day"
    else:
        usage, usage_key, avg_key = svc.ccf_used, "ccf", "average_ccf_per_day"
    return {
        "total": _f(svc.total_current_charges),
        usage_key: int(usage) if usage is not None else None,
        "days": svc.billing_days,
        avg_key: _f(_per_day(usage, svc.billing_days)),
        "effective_rate": _f(_eff_rate(svc.total_current_charges, usage)),
    }


def build_redacted_payload(db, bill: Bill) -> dict:
    """The ONLY data ever sent to the LLM. Whitelist of non-PII structured metrics.

    When the user has more than one bill, a ``history`` block is added with the *previous*
    bill's comparable metrics, the percent changes vs that bill, and rolling averages — so
    the model can describe real changes instead of saying "only one bill is available".
    No PII (account/meter/name/address) is ever included.
    """
    payload = {
        "bill_date": iso(bill.bill_date),
        "total_amount_due": _f(bill.total_amount_due),
        "electric": _service_metrics(bill.electric_service, "electric"),
        "gas": _service_metrics(bill.gas_service, "gas"),
        # Parser warning *codes* only (PII-free; never the full messages, which can embed
        # meter readings).
        "warnings": [w.warning_code for w in bill.warnings if w.warning_code],
    }

    bills = bill_service.bills_for_user(db, bill.user_id)
    if len(bills) > 1:
        idx = next((i for i, b in enumerate(bills) if b.id == bill.id), None)
        prev = bills[idx - 1] if (idx is not None and idx > 0) else None
        history: dict = {
            "bill_count": len(bills),
            "rolling_average_total_bill": _f(_avg_dec([b.total_amount_due for b in bills], "0.01")),
            "rolling_average_kwh_per_day": _f(
                _avg_dec(
                    [_per_day(b.electric_service.kwh_used, b.electric_service.billing_days)
                     for b in bills if b.electric_service],
                    "0.01",
                )
            ),
            "rolling_average_ccf_per_day": _f(
                _avg_dec(
                    [_per_day(b.gas_service.ccf_used, b.gas_service.billing_days)
                     for b in bills if b.gas_service],
                    "0.01",
                )
            ),
            "previous_bill": None,
            "changes_vs_previous": None,
        }
        if prev is not None:
            pe = prev.electric_service
            pg = prev.gas_service
            history["previous_bill"] = {
                "bill_date": iso(prev.bill_date),
                "total_amount_due": _f(prev.total_amount_due),
                "electric": _service_metrics(pe, "electric"),
                "gas": _service_metrics(pg, "gas"),
            }
            history["changes_vs_previous"] = {
                "total_bill_change_percent": _pct_change(prev.total_amount_due, bill.total_amount_due),
                "kwh_change_percent": _pct_change(
                    pe.kwh_used if pe else None,
                    bill.electric_service.kwh_used if bill.electric_service else None,
                ),
                "ccf_change_percent": _pct_change(
                    pg.ccf_used if pg else None,
                    bill.gas_service.ccf_used if bill.gas_service else None,
                ),
            }
        payload["history"] = history

    return payload


def deterministic_bill_summary(bill: Bill) -> str:
    """Deterministic, numerically-exact summary for a single bill (always available)."""
    e, g = bill.electric_service, bill.gas_service
    parts = [
        f"This bill is {_money_str(bill.total_amount_due)}, with "
        f"{_money_str(bill.electric_service_total)} for electric service and "
        f"{_money_str(bill.gas_service_total)} for gas service."
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
    if bill.warnings:
        parts.append(f"The parser flagged {len(bill.warnings)} validation warning(s) on this bill.")
    return " ".join(parts)


def _user_prompt(payload: dict) -> str:
    history = payload.get("history") or {}
    has_prev = bool(history.get("previous_bill"))
    if has_prev:
        notable = (
            "2. 'Notable changes:' — a short bullet list describing how THIS bill compares "
            "to the previous bill, using ONLY the numbers in the 'history' object (the "
            "'changes_vs_previous' percentages and the 'rolling_average_*' values; e.g. "
            "whether usage/cost rose or fell vs last period and vs the rolling average). "
            "Do not invent any numbers beyond those provided.\n"
        )
        trend_rule = (
            "you MAY compare to the previous bill and the rolling averages provided, but do "
            "not invent any other trend or numbers"
        )
    else:
        notable = (
            "2. 'Notable changes:' — a short bullet list of noteworthy aspects of this bill "
            "(e.g. the electric vs gas split). Only one bill is available, so explicitly "
            "state that comparisons to previous periods are not available.\n"
        )
        trend_rule = "do not claim historical trends or compare to other billing periods"

    return (
        "Summarize this CoServ utility bill using ONLY the JSON data below.\n\n"
        "Output exactly these three sections:\n"
        "1. A one-paragraph plain-language summary of THIS bill.\n"
        f"{notable}"
        "3. 'Questions to investigate:' — a short bullet list.\n\n"
        f"Rules: no financial advice; no unsupported claims; do not invent weather context; "
        f"{trend_rule}; do not mention any personal information; use the exact numbers given.\n\n"
        f"Bill data (JSON):\n{json.dumps(payload, indent=2)}"
    )


def generate_llm_summary(
    payload: dict, *, model: str, host: str, timeout: float
) -> Tuple[Optional[str], str]:
    """Call Ollama's local chat API. Returns (text, status).

    status is one of: 'ok', 'unavailable' (not running / network / timeout), 'error'
    (bad response / model missing). Never raises — callers fall back on failure.
    """
    try:
        import httpx
    except Exception:  # httpx not installed
        return None, "unavailable"

    body = {
        "model": model,
        "stream": False,
        "options": {"temperature": 0.2},  # low temperature for numeric faithfulness
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_prompt(payload)},
        ],
    }
    try:
        resp = httpx.post(f"{host.rstrip('/')}/api/chat", json=body, timeout=timeout)
    except Exception:
        logger.info("Ollama unavailable (connection/timeout); falling back to deterministic.")
        return None, "unavailable"

    if resp.status_code != 200:
        logger.info("Ollama returned HTTP %s; falling back to deterministic.", resp.status_code)
        return None, "error"
    try:
        content = (resp.json().get("message") or {}).get("content", "").strip()
    except Exception:
        return None, "error"
    if not content:
        return None, "error"
    return content, "ok"


def summarize_bill(db, bill: Bill) -> dict:
    """Fast, non-blocking summary payload.

    Always returns the deterministic summary immediately. It does NOT call the LLM — when
    Ollama is enabled the client streams the LLM text separately via the stream endpoint,
    so a slow model can never block this response (or the dashboard).
    """
    settings = get_settings()
    return {
        "bill_id": bill.id,
        "deterministic_summary": deterministic_bill_summary(bill),
        "llm_enabled": settings.ollama_enabled,
        "llm_model": ollama_runtime.get_active_model() if settings.ollama_enabled else None,
        "llm_summary": None,  # streamed separately when enabled (see stream_llm_summary)
        "llm_status": "stream" if settings.ollama_enabled else "disabled",
        "redacted_input": build_redacted_payload(db, bill),  # transparency — no PII
    }


def stream_ollama_chat(messages: list, *, model: str, host: str, timeout: float):
    """Generic Ollama streaming chat: yield text deltas for arbitrary ``messages``.

    Yields nothing if Ollama is unavailable/errors (callers fall back). Never raises.
    Used by both the per-bill summary stream and the assistant chat.
    """
    try:
        import httpx
    except Exception:
        return

    body = {
        "model": model,
        "stream": True,
        "options": {"temperature": 0.2},  # low temperature for numeric faithfulness
        "messages": messages,
    }
    try:
        with httpx.stream("POST", f"{host.rstrip('/')}/api/chat", json=body, timeout=timeout) as resp:
            if resp.status_code != 200:
                logger.info("Ollama stream HTTP %s; client falls back.", resp.status_code)
                return
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                chunk = (obj.get("message") or {}).get("content", "")
                if chunk:
                    yield chunk
                if obj.get("done"):
                    break
    except Exception:
        logger.info("Ollama stream unavailable; client falls back.")
        return


def stream_llm_summary(payload: dict, *, model: str, host: str, timeout: float):
    """Yield LLM text deltas for a single-bill summary (delegates to stream_ollama_chat)."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _user_prompt(payload)},
    ]
    yield from stream_ollama_chat(messages, model=model, host=host, timeout=timeout)


@lru_cache(maxsize=64)
def supports_thinking(model: str, host: str) -> bool:
    """Whether a model exposes a reasoning/'thinking' channel (via /api/show capabilities)."""
    try:
        import httpx
    except Exception:
        return False
    try:
        resp = httpx.post(f"{host.rstrip('/')}/api/show", json={"model": model}, timeout=8.0)
        if resp.status_code != 200:
            return False
        caps = resp.json().get("capabilities") or []
        return "thinking" in caps
    except Exception:
        return False


def stream_ollama_events(messages: list, *, model: str, host: str, timeout: float, think: bool = False):
    """Yield structured events: ``{"t": "think"|"answer"|"status", "d": "<text>"}``.

    When ``think`` is True and the model supports it, the model's reasoning streams as
    'think' events (so the UI can show what it's working on); the final answer streams as
    'answer' events. Yields a 'status' event and stops on any failure (never raises).
    """
    try:
        import httpx
    except Exception:
        yield {"t": "status", "d": "Local model library unavailable."}
        return

    body = {
        "model": model,
        "stream": True,
        "options": {"temperature": 0.2},
        "messages": messages,
    }
    if think:
        body["think"] = True

    try:
        with httpx.stream("POST", f"{host.rstrip('/')}/api/chat", json=body, timeout=timeout) as resp:
            if resp.status_code != 200:
                yield {"t": "status", "d": f"Local model error (HTTP {resp.status_code})."}
                return
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                msg = obj.get("message") or {}
                thinking = msg.get("thinking")
                if thinking:
                    yield {"t": "think", "d": thinking}
                content = msg.get("content")
                if content:
                    yield {"t": "answer", "d": content}
                if obj.get("done"):
                    break
    except Exception:
        yield {"t": "status", "d": "Local model unavailable (connection or timeout)."}
        return
