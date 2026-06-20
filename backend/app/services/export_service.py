"""Reporting & export: JSON, CSV, and a PDF monthly summary.

Every export reflects the **corrected (effective)** bill data — the same overlay used by
the UI and analytics — so what a user downloads matches what they see.

Privacy: exports are *scrubbed* of sensitive fields (account last-4, redacted address) by
default. They are included only when the caller passes ``include_sensitive=true`` AND the
backend is configured with ``ALLOW_SENSITIVE_EXPORT=true``; otherwise the flag is ignored.
(The system never stores full account numbers or addresses, so those can never be exported.)
"""

from __future__ import annotations

import csv
import io
import logging
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Bill
from .bill_service import d_str, iso, usage_num
from . import (
    analytics_service,
    bill_audit_service,
    bill_comparison_service,
    bill_service,
    correction_service,
    rate_breakdown_service,
)

logger = logging.getLogger("hui.export")

# Sensitive keys scrubbed from JSON exports unless sensitive export is allowed.
_SENSITIVE_SUMMARY_KEYS = (
    "account_number_last4",
    "account_number_redacted",
    "service_address_redacted",
)


# --------------------------------------------------------------------------------------
# Sensitivity gating
# --------------------------------------------------------------------------------------


def sensitive_allowed(include_sensitive: bool) -> bool:
    """Sensitive fields are exported only when requested AND permitted by config AND not
    blocked by local-only privacy mode."""
    from ..security import privacy

    return (
        bool(include_sensitive)
        and bool(get_settings().allow_sensitive_export)
        and not privacy.local_only()
    )


def _scrub_bill_json(data: dict, allow: bool) -> dict:
    """Remove sensitive fields from a serialized bill unless ``allow`` is True."""
    if allow:
        return data
    summary = data.get("account_summary")
    if isinstance(summary, dict):
        for k in _SENSITIVE_SUMMARY_KEYS:
            if k in summary:
                summary[k] = None
    ua = data.get("utility_account")
    if isinstance(ua, dict):
        ua["account_number_last4"] = None
        ua["service_address_redacted"] = None
    return data


# --------------------------------------------------------------------------------------
# JSON exports
# --------------------------------------------------------------------------------------


def _corrected_bills(db: Session) -> list[Bill]:
    user = bill_service.get_or_create_default_user(db)
    return bill_service.bills_for_user(db, user.id, apply_corrections=True)


def bills_json(db: Session, *, include_sensitive: bool = False) -> list[dict]:
    allow = sensitive_allowed(include_sensitive)
    return [_scrub_bill_json(bill_service.serialize_bill(b), allow) for b in _corrected_bills(db)]


def bill_json(db: Session, bill_id: int, *, include_sensitive: bool = False) -> Optional[dict]:
    eff = correction_service.get_effective_bill_data(db, bill_id)
    if eff is None:
        return None
    bill, corrections, corrected_paths = eff
    data = correction_service.serialize_effective_bill(bill, corrections, corrected_paths)
    return _scrub_bill_json(data, sensitive_allowed(include_sensitive))


# --------------------------------------------------------------------------------------
# CSV exports
# --------------------------------------------------------------------------------------


def _to_csv(header: list[str], rows: list[list[Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(["" if v is None else v for v in row])
    return buf.getvalue()


def bills_csv(db: Session) -> str:
    header = [
        "bill_date", "due_date", "billing_days", "total_amount_due",
        "electric_service_total", "gas_service_total", "current_charges_total",
        "amount_due_after_due_date", "autopay_status", "parser_confidence",
    ]
    rows = [
        [
            iso(b.bill_date), iso(b.due_date), b.billing_days,
            d_str(b.total_amount_due), d_str(b.electric_service_total),
            d_str(b.gas_service_total), d_str(b.current_charges_total),
            d_str(b.amount_due_after_due_date), b.autopay_status, b.parser_confidence,
        ]
        for b in _corrected_bills(db)
    ]
    return _to_csv(header, rows)


def electric_csv(db: Session) -> str:
    header = [
        "bill_date", "service_start_date", "service_end_date", "billing_days",
        "kwh_used", "kwh_per_day", "total_charges", "effective_rate_per_kwh",
        "average_cost_per_day",
    ]
    rows = []
    for b in _corrected_bills(db):
        e = b.electric_service
        if e is None:
            continue
        rows.append([
            iso(b.bill_date), iso(e.service_start_date), iso(e.service_end_date),
            e.billing_days, usage_num(e.kwh_used), d_str(e.average_kwh_per_day),
            d_str(e.total_current_charges), d_str(e.effective_rate_per_kwh),
            d_str(e.average_cost_per_day),
        ])
    return _to_csv(header, rows)


def gas_csv(db: Session) -> str:
    header = [
        "bill_date", "service_start_date", "service_end_date", "billing_days",
        "ccf_used", "ccf_per_day", "total_charges", "effective_rate_per_ccf",
        "average_cost_per_day",
    ]
    rows = []
    for b in _corrected_bills(db):
        g = b.gas_service
        if g is None:
            continue
        rows.append([
            iso(b.bill_date), iso(g.service_start_date), iso(g.service_end_date),
            g.billing_days, usage_num(g.ccf_used), d_str(g.average_ccf_per_day),
            d_str(g.total_current_charges), d_str(g.effective_rate_per_ccf),
            d_str(g.average_cost_per_day),
        ])
    return _to_csv(header, rows)


def charges_csv(db: Session) -> str:
    header = ["bill_date", "service_type", "label", "quantity", "unit", "rate", "amount", "is_credit"]
    rows = []
    for b in _corrected_bills(db):
        for li in sorted(b.line_items, key=lambda x: (x.service_type, x.id)):
            rows.append([
                iso(b.bill_date), li.service_type, li.label,
                usage_num(li.quantity), li.unit, d_str(li.rate), d_str(li.amount),
                "true" if li.is_credit else "false",
            ])
    return _to_csv(header, rows)


# --------------------------------------------------------------------------------------
# PDF monthly summary report
# --------------------------------------------------------------------------------------


def _safe(text: Any) -> str:
    """Latin-1 safe text for fpdf core fonts (replaces unencodable chars)."""
    s = "" if text is None else str(text)
    return s.encode("latin-1", "replace").decode("latin-1")


def _money(v) -> str:
    if v is None:
        return "-"
    dec = v if isinstance(v, Decimal) else Decimal(str(v))
    sign = "-" if dec < 0 else ""
    return f"{sign}${abs(dec):,.2f}"


def bill_report_pdf(db: Session, bill_id: int, *, include_sensitive: bool = False) -> Optional[bytes]:
    """A one-page PDF summary for a bill: summary, electric/gas, rate breakdown, audit,
    confidence, what-changed, anomalies, and weather context (each when available)."""
    from fpdf import FPDF

    eff = correction_service.get_effective_bill_data(db, bill_id)
    if eff is None:
        return None
    bill, _corrections, _paths = eff
    allow = sensitive_allowed(include_sensitive)

    pdf = FPDF(format="letter", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    def h1(text: str) -> None:
        pdf.set_font("Helvetica", "B", 15)
        pdf.cell(0, 9, _safe(text), new_x="LMARGIN", new_y="NEXT")

    def h2(text: str) -> None:
        pdf.ln(1.5)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 7, _safe(text), new_x="LMARGIN", new_y="NEXT")

    def kv(label: str, value: str) -> None:
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(60, 5.5, _safe(label))
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 5.5, _safe(value), new_x="LMARGIN", new_y="NEXT")

    def line(text: str) -> None:
        pdf.set_font("Helvetica", "", 9)
        # new_x=LMARGIN is essential: multi_cell otherwise leaves the cursor at the right
        # margin, so the next full-width multi_cell would have no horizontal space.
        pdf.multi_cell(0, 5, _safe(text), new_x="LMARGIN", new_y="NEXT")

    h1("CoServ Bill Report")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(120)
    pdf.cell(0, 5, _safe(f"Bill #{bill.id} - statement {iso(bill.bill_date) or 'unknown'}"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0)

    # Bill summary
    h2("Bill summary")
    kv("Statement date", iso(bill.bill_date) or "-")
    kv("Due date", iso(bill.due_date) or "-")
    kv("Billing days", str(bill.billing_days or "-"))
    kv("Total amount due", _money(bill.total_amount_due))
    kv("Electric service total", _money(bill.electric_service_total))
    kv("Gas service total", _money(bill.gas_service_total))
    kv("Parser confidence", bill.parser_confidence or "-")
    if allow and bill.utility_account is not None:
        kv("Account (last 4)", bill.utility_account.account_number_last4 or "-")
        kv("Service address", bill.utility_account.service_address_redacted or "-")

    # Electric / gas
    e = bill.electric_service
    if e is not None:
        h2("Electric")
        kv("Usage", f"{usage_num(e.kwh_used)} kWh" if e.kwh_used is not None else "-")
        kv("Avg per day", f"{d_str(e.average_kwh_per_day) or '-'} kWh/day")
        kv("Effective rate", f"${d_str(e.effective_rate_per_kwh) or '-'}/kWh")
        kv("Total charges", _money(e.total_current_charges))
    g = bill.gas_service
    if g is not None:
        h2("Gas")
        kv("Usage", f"{usage_num(g.ccf_used)} CCF" if g.ccf_used is not None else "-")
        kv("Avg per day", f"{d_str(g.average_ccf_per_day) or '-'} CCF/day")
        kv("Effective rate", f"${d_str(g.effective_rate_per_ccf) or '-'}/CCF")
        kv("Total charges", _money(g.total_current_charges))

    # Rate breakdown (categories per service)
    try:
        rb = rate_breakdown_service.breakdown_dict(bill)
        for svc_key in ("electric", "gas"):
            svc = rb.get(svc_key)
            if not svc:
                continue
            h2(f"Rate breakdown - {svc_key}")
            for cat in svc.get("categories", []):
                amt = cat.get("amount")
                if amt in (None, "0", "0.00"):
                    continue
                line(f"- {cat.get('label', cat.get('key'))}: {_money(amt)}")
    except Exception:  # pragma: no cover - report must never fail on an optional section
        logger.warning("rate breakdown omitted from report for bill %s", bill_id)

    # Audit status + confidence
    try:
        audit = bill_audit_service.run_audit(bill)
        h2("Audit")
        kv("Overall status", str(audit.overall_status))
        counts: dict[str, int] = {}
        for c in audit.checks:
            counts[c.status] = counts.get(c.status, 0) + 1
        line("Checks: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    except Exception:  # pragma: no cover
        logger.warning("audit omitted from report for bill %s", bill_id)

    # What changed vs previous bill
    try:
        cmp = bill_comparison_service.compare_bill(db, bill_id)
        if cmp.status == "ok":
            h2("What changed vs previous bill")
            for ln in cmp.narrative.lines:
                line(ln)
    except Exception:  # pragma: no cover
        logger.warning("comparison omitted from report for bill %s", bill_id)

    # Anomalies (portfolio-level, if any)
    try:
        user = bill_service.get_or_create_default_user(db)
        anom = analytics_service.detect_basic_anomalies(db, user.id)
        flags = anom.get("anomalies") or []
        if flags:
            h2("Anomalies")
            for f in flags:
                line(f"- {f.get('message', '')}")
    except Exception:  # pragma: no cover
        logger.warning("anomalies omitted from report for bill %s", bill_id)

    # Weather context
    try:
        from . import weather_normalization_service

        wn = weather_normalization_service.normalize_bill(db, bill_id)
        if wn is not None and wn.status == "ok":
            h2("Weather context")
            line(wn.electric_usage_vs_cdd_context)
            line(wn.gas_usage_vs_hdd_context)
    except Exception:  # pragma: no cover
        logger.warning("weather omitted from report for bill %s", bill_id)

    out = pdf.output()  # fpdf2 returns a bytearray
    return bytes(out)
