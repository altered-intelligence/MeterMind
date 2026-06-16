"""Assistant chat: answer the user's questions over ALL their structured data.

Design for extensibility: each data source registers a **context provider** — a function
``(db, user_id) -> dict`` returning redacted, structured metrics. The assistant sees the
union of every registered provider, so adding a new source later (weather, Tesla, water,
…) is just: write a provider and ``register_context_provider("weather", fn)`` — no changes
to the chat endpoint or prompt.

Privacy: providers must emit only redacted metrics (no names, account/meter numbers,
addresses, payment data, or raw document text). The CoServ provider below already does.
The LLM only reasons over what providers supply; numbers come from the deterministic
pipeline.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Callable, List, Optional

from sqlalchemy.orm import Session

from . import bill_service
from .analytics_service import _avg_dec, _eff_rate, _per_day
from .bill_service import iso, usage_num

# Bound the per-source context so a huge history can't blow the model's context window.
MAX_BILLS_IN_CONTEXT = 120

ContextProvider = Callable[[Session, int], Optional[dict]]
_PROVIDERS: "dict[str, ContextProvider]" = {}


def register_context_provider(name: str, fn: ContextProvider) -> None:
    """Register a data source. Future sources (weather, tesla, …) call this."""
    _PROVIDERS[name] = fn


def registered_sources() -> List[str]:
    return sorted(_PROVIDERS)


def _f(value) -> Optional[float]:
    return None if value is None else float(Decimal(str(value)))


def _coserv_bills_provider(db: Session, user_id: int) -> Optional[dict]:
    """Redacted CoServ electric + gas history (per-bill rows + aggregates)."""
    bills = bill_service.bills_for_user(db, user_id)
    if not bills:
        return {"description": "CoServ electric & gas bills", "bill_count": 0, "bills": []}

    rows = []
    for b in bills[-MAX_BILLS_IN_CONTEXT:]:
        e, g = b.electric_service, b.gas_service
        rows.append(
            {
                "bill_date": iso(b.bill_date),
                "total_amount_due": _f(b.total_amount_due),
                "electric_total": _f(b.electric_service_total),
                "gas_total": _f(b.gas_service_total),
                "avg_total_cost_per_day": _f(_per_day(b.total_amount_due, b.billing_days)),
                "electric": (
                    {
                        "kwh": usage_num(e.kwh_used),
                        "days": e.billing_days,
                        "avg_kwh_per_day": _f(_per_day(e.kwh_used, e.billing_days)),
                        "avg_cost_per_day": _f(_per_day(e.total_current_charges, e.billing_days)),
                        "effective_rate_per_kwh": _f(_eff_rate(e.total_current_charges, e.kwh_used)),
                    }
                    if e
                    else None
                ),
                "gas": (
                    {
                        "ccf": usage_num(g.ccf_used),
                        "days": g.billing_days,
                        "avg_ccf_per_day": _f(_per_day(g.ccf_used, g.billing_days)),
                        "avg_cost_per_day": _f(_per_day(g.total_current_charges, g.billing_days)),
                        "effective_rate_per_ccf": _f(_eff_rate(g.total_current_charges, g.ccf_used)),
                    }
                    if g
                    else None
                ),
            }
        )

    def _peak(key):
        vals = [(v, b) for b in bills if (v := key(b)) is not None]
        return max(vals, key=lambda vb: vb[0])[1] if vals else None

    highest_total = _peak(lambda b: b.total_amount_due)
    highest_kwh = _peak(lambda b: b.electric_service.kwh_used if b.electric_service else None)
    highest_ccf = _peak(lambda b: b.gas_service.ccf_used if b.gas_service else None)

    return {
        "description": "CoServ electric & gas bills (one statement per ~monthly cycle)",
        "units": {"money": "USD", "electricity": "kWh", "gas": "CCF (hundreds of cubic feet)"},
        "bill_count": len(bills),
        "date_range": [iso(bills[0].bill_date), iso(bills[-1].bill_date)],
        "aggregates": {
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
            "rolling_average_total_cost_per_day": _f(
                _avg_dec([_per_day(b.total_amount_due, b.billing_days) for b in bills], "0.01")
            ),
            "rolling_average_electric_cost_per_day": _f(
                _avg_dec(
                    [_per_day(b.electric_service.total_current_charges, b.electric_service.billing_days)
                     for b in bills if b.electric_service],
                    "0.01",
                )
            ),
            "rolling_average_gas_cost_per_day": _f(
                _avg_dec(
                    [_per_day(b.gas_service.total_current_charges, b.gas_service.billing_days)
                     for b in bills if b.gas_service],
                    "0.01",
                )
            ),
            "highest_total_bill": (
                {"bill_date": iso(highest_total.bill_date), "total_amount_due": _f(highest_total.total_amount_due)}
                if highest_total
                else None
            ),
            "highest_electric_usage": (
                {"bill_date": iso(highest_kwh.bill_date), "kwh": usage_num(highest_kwh.electric_service.kwh_used)}
                if highest_kwh
                else None
            ),
            "highest_gas_usage": (
                {"bill_date": iso(highest_ccf.bill_date), "ccf": usage_num(highest_ccf.gas_service.ccf_used)}
                if highest_ccf
                else None
            ),
        },
        "bills": rows,
    }


register_context_provider("coserv_bills", _coserv_bills_provider)


def build_context(db: Session, user_id: int) -> dict:
    """Union of every registered provider's redacted data."""
    sources: dict = {}
    for name, fn in _PROVIDERS.items():
        try:
            out = fn(db, user_id)
        except Exception:
            out = None
        if out is not None:
            sources[name] = out
    return {"sources": sources}


ASSISTANT_SYSTEM_PROMPT = (
    "You are a careful assistant embedded in a personal home-utility dashboard. Answer the "
    "user's questions using ONLY the structured data provided below. The data is already "
    "parsed, validated, and redacted — it contains no names, account numbers, addresses, or "
    "meter numbers, so never mention or guess personal information.\n"
    "Rules: be concise and numerically accurate; use the exact figures in the data. "
    "IMPORTANT: when a pre-computed value already answers the question (the 'aggregates' "
    "block has rolling averages, peaks, and totals; each bill has avg-per-day and "
    "effective-rate fields), READ that value directly — do NOT re-derive it with your own "
    "multi-step arithmetic, which is error-prone. If the answer is not present, say so "
    "plainly instead of guessing; do not invent numbers, weather, or trends. Do not give "
    "financial, legal, or tax advice. Money is USD, electricity is kWh, and gas is CCF "
    "(hundreds of cubic feet). More data sources may appear under 'sources' over time — "
    "only use what is present."
)


def build_messages(context: dict, conversation: List[dict]) -> List[dict]:
    """Assemble Ollama chat messages: system (prompt + data) followed by the conversation."""
    system = (
        ASSISTANT_SYSTEM_PROMPT
        + "\n\nUser's data (JSON):\n"
        + json.dumps(context, indent=2)
        + "\n\nAnswer using ONLY this data."
    )
    messages = [{"role": "system", "content": system}]
    for m in conversation:
        role = m.get("role")
        content = m.get("content")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": str(content)[:4000]})
    return messages
