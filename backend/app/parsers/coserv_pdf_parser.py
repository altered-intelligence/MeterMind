"""Deterministic parser for CoServ combined (electric + gas) utility bill PDFs.

Text is extracted with :mod:`pdfplumber`. OCR is attempted *only* when text extraction
yields nothing (e.g. a scanned image PDF), and only if optional OCR dependencies are
installed — it is never used for normal text PDFs. No LLM is involved at any point.

Design notes
------------
* Extraction is split into ``_extract_text`` (PDF -> string) and ``_parse_bill_text``
  (string -> :class:`CoservBill`). The text parser is pure and side-effect free, which
  keeps it deterministic and unit-testable.
* Regexes are deliberately whitespace- and synonym-tolerant. CoServ tweaks the exact
  wording/spacing month to month, so labels are matched loosely and amounts are captured
  as the trailing money token on a line.
* The meter/period/reading data sits on a single tabular row in real CoServ PDFs:
    {meter#}  {from}  {to}  {days}  {last_read}  {current_read}  {mult}  {usage}
  ``_parse_meter_table_row`` handles this; individual-field regexes are the fallback.
* Charge-line labels such as "Customer Charge" appear in *both* service sections, so
  the document is sliced into per-service regions before line items are extracted.
* ``_amount_on_line`` uses a greedy match so it captures the LAST dollar amount on the
  line.  Lines like "Energy Charge 1,725 kWh $212.66" carry a usage quantity before the
  actual charge; a non-greedy match would wrongly return the usage number.
* No sample-bill values are hard-coded. Nothing here logs raw PDF text — ``--debug``
  only ever emits *redacted* blocks via :func:`redact_text`.

CLI::

    python -m app.parsers.coserv_pdf_parser bill.pdf            # prints JSON
    python -m app.parsers.coserv_pdf_parser --debug bill.pdf    # + redacted text blocks
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import List, Optional, Tuple

import pdfplumber

try:  # Works when ``backend/`` is on sys.path (package import or installed).
    from app.schemas import (
        CoservAccountSummary,
        CoservBill,
        CoservChargeLineItem,
        CoservElectricService,
        CoservGasService,
        CoservParserWarning,
        CoservSnapshot,
        ParserConfidence,
        WarningSeverity,
    )
except ModuleNotFoundError:  # Allow ``python coserv_pdf_parser.py`` direct execution.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.schemas import (
        CoservAccountSummary,
        CoservBill,
        CoservChargeLineItem,
        CoservElectricService,
        CoservGasService,
        CoservParserWarning,
        CoservSnapshot,
        ParserConfidence,
        WarningSeverity,
    )

PARSER_VERSION = "coserv-v0.1.0"
# The shape/contract of the parsed CoservBill. Bumped only when the output schema changes
# (new/removed/renamed fields), independently of PARSER_VERSION (which tracks extraction
# logic). Stored on each bill + parser run so a layout change is auditable.
SCHEMA_VERSION = "coserv-bill-schema-v1"

# --------------------------------------------------------------------------------------
# Low-level token patterns / normalizers
# --------------------------------------------------------------------------------------

# A money token: optional paren/sign/$, grouped digits, optional cents, optional CR/DR.
# Used by normalize_money() which must accept all formats including bare numbers.
_MONEY = r"\(?\s*-?\s*\$?\s*\d[\d,]*(?:\.\d{1,2})?\s*\)?(?:\s*(?:CR|DR))?"

# A dollar amount that REQUIRES the "$" sign AND uses only horizontal whitespace ([ \t]).
# Using \s* would let _DOLLAR start with a newline, causing greedy .* to cross the
# line boundary and capture the next line's amount as the "last" token.
_DOLLAR = r"\(?[ \t]*-?[ \t]*\$[ \t]*\d[\d,]*(?:\.\d{1,2})?[ \t]*\)?(?:[ \t]*(?:CR|DR))?"

# A date token: numeric (m/d/y) or month-name style.
_DATE = r"(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})"

_REDACTED = "[REDACTED]"


def normalize_money(raw: Optional[str]) -> Optional[Decimal]:
    """Convert a money string to a :class:`Decimal`, with credits as negatives.

    Handles ``"$317.00"``, ``"$274.00 CR"``, ``"($10.35)"``, ``"-$10.35"`` and
    thousands separators. Returns ``None`` if no numeric value is present.
    """
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    m = re.search(r"\d[\d,]*(?:\.\d+)?", s)
    if not m:
        return None
    try:
        value = Decimal(m.group(0).replace(",", ""))
    except InvalidOperation:
        return None
    upper = s.upper()
    negative = (
        "(" in s
        or s.lstrip().startswith("-")
        or upper.rstrip().endswith("CR")
    )
    return -value if negative else value


def parse_date(raw: Optional[str]) -> Optional[date]:
    """Parse a date string into a :class:`datetime.date`, or ``None``."""
    if not raw:
        return None
    s = raw.strip().rstrip(".,")
    for fmt in (
        "%m/%d/%Y",
        "%m/%d/%y",
        "%m-%d-%Y",
        "%m-%d-%y",
        "%Y-%m-%d",
        "%B %d, %Y",
        "%b %d, %Y",
        "%B %d %Y",
        "%b %d %Y",
    ):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_int(raw: Optional[str]) -> Optional[int]:
    """Parse an integer (commas allowed), or ``None``."""
    if raw is None:
        return None
    m = re.search(r"-?\d[\d,]*", raw)
    if not m:
        return None
    try:
        return int(m.group(0).replace(",", ""))
    except ValueError:
        return None


def parse_usage(raw: Optional[str]):
    """Parse a usage reading: ``int`` when whole, ``Decimal`` only if fractional."""
    if raw is None:
        return None
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", raw)
    if not m:
        return None
    token = m.group(0).replace(",", "")
    if "." in token:
        try:
            return Decimal(token)
        except InvalidOperation:
            return None
    try:
        return int(token)
    except ValueError:
        return None


def parse_decimal(raw: Optional[str]) -> Optional[Decimal]:
    """Parse a plain decimal (e.g. a meter multiplier), or ``None``."""
    if raw is None:
        return None
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", raw)
    if not m:
        return None
    try:
        return Decimal(m.group(0).replace(",", ""))
    except InvalidOperation:
        return None


# --------------------------------------------------------------------------------------
# Warning helpers
# --------------------------------------------------------------------------------------


def _warn(
    warnings: List[CoservParserWarning],
    code: str,
    message: str,
    field: Optional[str] = None,
    severity: WarningSeverity = WarningSeverity.WARNING,
) -> None:
    warnings.append(
        CoservParserWarning(code=code, message=message, field=field, severity=severity)
    )


def _require(value, warnings, field, label, severity=WarningSeverity.WARNING):
    """Emit a warning when a value the caller considers required is missing."""
    if value is None or (isinstance(value, str) and not value.strip()):
        _warn(warnings, "missing_field", f"Could not extract {label}.", field, severity)
    return value


# --------------------------------------------------------------------------------------
# Generic line-oriented extraction helpers
# --------------------------------------------------------------------------------------


def _label_to_pattern(label: str) -> str:
    """Turn a human label into a whitespace-tolerant regex fragment."""
    return r"\s+".join(re.escape(word) for word in label.split())


def _amount_on_line(region: str, label: str) -> Optional[Decimal]:
    """Find ``label`` near the start of a line and return the LAST dollar amount on it.

    Uses ``_DOLLAR`` (requires ``$``) rather than ``_MONEY`` (optional ``$``) so that
    greedy backtracking cannot mistake a bare bar-chart integer for the charge amount.
    The greedy ``.*`` ensures we get the rightmost dollar token (e.g. on
    ``"Energy Charge 1,725 kWh $212.66"`` we skip ``1,725`` and capture ``$212.66``).
    """
    lab = _label_to_pattern(label)
    line_anchored = re.compile(rf"(?im)^\s*{lab}\b.*({_DOLLAR})[ \t]*$")
    m = line_anchored.search(region)
    if not m:
        # Looser: label anywhere on line, last dollar amount before EOL.
        loose = re.compile(rf"(?im){lab}\b[^\n]*({_DOLLAR})[ \t]*$")
        m = loose.search(region)
    return normalize_money(m.group(1)) if m else None


def _search(region: str, pattern: str, flags=re.IGNORECASE) -> Optional[re.Match]:
    return re.search(pattern, region, flags)


def _group(region: str, pattern: str, flags=re.IGNORECASE) -> Optional[str]:
    m = re.search(pattern, region, flags)
    return m.group(1).strip() if m else None


# --------------------------------------------------------------------------------------
# Section slicing
# --------------------------------------------------------------------------------------


def _find_detail_header(text: str, service_word: str) -> Optional[int]:
    """Return the offset of the *detail* "<service> Service" header.

    The Account Summary line ("Electric Service $239.14") is excluded because it
    carries a money amount on the same line.  Among bare headings, one followed shortly
    by meter / reading / period metadata is preferred; otherwise the first bare heading
    is returned.
    """
    pat = re.compile(rf"(?im)^[ \t]*{service_word}\s+service\b([^\n]*)$")
    fallback = None
    for m in pat.finditer(text):
        if re.search(r"\d", m.group(1)):
            continue  # summary line carries an amount — not the detail header
        if fallback is None:
            fallback = m.start()
        window = text[m.start() : m.start() + 400].lower()
        if any(k in window for k in ("meter", "reading", "service period")):
            return m.start()
    return fallback


def _snapshot_start(text: str) -> Optional[int]:
    m = re.search(r"(?i)(?:usage\s+)?snapshot", text)
    if m:
        return m.start()
    m = re.search(r"(?i)average\s+cost\s*(?:per|/)\s*day", text)
    return m.start() if m else None


def _slice_sections(text: str) -> Tuple[Optional[str], Optional[str]]:
    """Split text into (electric_detail_region, gas_detail_region)."""
    elec_pos = _find_detail_header(text, "electric")
    gas_pos = _find_detail_header(text, "gas")
    snap_pos = _snapshot_start(text)
    end = len(text)

    electric_region = None
    gas_region = None

    if elec_pos is not None:
        elec_end = end
        for candidate in (gas_pos, snap_pos):
            if candidate is not None and candidate > elec_pos:
                elec_end = min(elec_end, candidate)
        electric_region = text[elec_pos:elec_end]

    if gas_pos is not None:
        gas_end = end
        if snap_pos is not None and snap_pos > gas_pos:
            gas_end = snap_pos
        gas_region = text[gas_pos:gas_end]

    return electric_region, gas_region


# --------------------------------------------------------------------------------------
# Account summary
# --------------------------------------------------------------------------------------


def _parse_autopay(text: str) -> Optional[str]:
    # Explicit "not enrolled" signals take priority.
    if re.search(r"(?i)not\s+(?:currently\s+)?enrolled\s+in\s+auto", text):
        return "not_enrolled"
    if re.search(r"(?i)auto.?pay\s+(?:is\s+)?not\b", text):
        return "not_enrolled"
    # "enrolled in autopay" (synthetic / older format)
    if re.search(r"(?i)enrolled\s+in\s+auto.?pay", text):
        return "enrolled"
    # "AutoPay is active/enabled/enrolled"
    if re.search(r"(?i)auto.?pay\s+(?:is\s+)?(?:active|enabled|enrolled)", text):
        return "enrolled"
    # "Auto-Pay" label + "Do Not Pay" stub = autopay is active on the real bill
    if re.search(r"(?i)auto.?pay", text) and re.search(r"(?i)do\s+not\s+pay", text):
        return "enrolled"
    # "Auto-Pay" label present without a negation signal → enrolled
    if re.search(r"(?i)auto.?pay", text):
        return "enrolled"
    return None


def _parse_account_summary(text: str, warnings: List[CoservParserWarning]) -> CoservAccountSummary:
    s = CoservAccountSummary()

    s.bill_date = parse_date(
        _group(text, rf"(?i)(?:bill|statement|invoice)\s*date\s*[:#]?\s*({_DATE})")
    )

    # Due date appears in several formats across CoServ bill versions.
    s.due_date = parse_date(
        _group(text, rf"(?i)(?:payment\s+)?due\s*date\s*[:#]?\s*({_DATE})")
        or _group(text, rf"(?i)(?:total\s+)?amount\s+due\s+by\s+({_DATE})")
        or _group(text, rf"(?i)payment\s+due\s+(?:on\s+)?({_DATE})")
        or _group(text, rf"(?i)due\s+(?:on|by)\s+({_DATE})")
    )

    s.previous_balance = _amount_on_line(text, "Previous Balance")
    s.balance_forward = _amount_on_line(text, "Balance Forward")
    # "Payment(s) Received" / "Payment Received - Thank You!" — greedy, $ required.
    pay = _search(text, rf"(?im)^\s*payments?\b.*received\b.*({_DOLLAR})[ \t]*$")
    s.payment_received = normalize_money(pay.group(1)) if pay else None

    s.electric_service_total = _amount_on_line(text, "Electric Service")
    s.gas_service_total = _amount_on_line(text, "Gas Service")
    s.current_charges_total = _amount_on_line(text, "Current Charges")
    s.total_amount_due = _amount_on_line(text, "Total Amount Due")

    # "Amount Due after {date} ${late_amount}" — greedy, $ required to skip the date.
    after = _search(text, rf"(?im)^\s*amount\s+due\s+after\b.*({_DOLLAR})[ \t]*$")
    s.amount_due_after_due_date = normalize_money(after.group(1)) if after else None

    s.autopay_status = _parse_autopay(text)

    acct = _group(
        text, r"(?i)account\s*(?:number|no\.?|#)\s*[:#]?\s*([0-9][0-9\- ]{2,}[0-9]|[0-9]+)"
    )
    s.account_number = acct.replace(" ", "") if acct else None

    # "Service Address: ..." at line start OR "Service Addr ..." mid-line (stub area).
    addr = (
        _group(text, r"(?im)^\s*service\s+addr(?:ess)?\s*[:#\-]?\s*(.+?)\s*$")
        or _group(text, r"(?i)service\s+addr(?:ess)?\b\s*[:#\-]?\s*([^\n]+)")
    )
    s.service_address = addr.strip() if addr else None

    # Required-for-high-confidence summary fields.
    _require(s.bill_date, warnings, "account_summary.bill_date", "bill date")
    _require(s.due_date, warnings, "account_summary.due_date", "due date")
    _require(s.total_amount_due, warnings, "account_summary.total_amount_due", "total amount due")
    _require(
        s.current_charges_total,
        warnings,
        "account_summary.current_charges_total",
        "current charges total",
    )
    _require(
        s.electric_service_total,
        warnings,
        "account_summary.electric_service_total",
        "electric service total",
    )
    _require(
        s.gas_service_total,
        warnings,
        "account_summary.gas_service_total",
        "gas service total",
    )
    _require(s.previous_balance, warnings, "account_summary.previous_balance", "previous balance")
    _require(s.balance_forward, warnings, "account_summary.balance_forward", "balance forward")
    _require(
        s.account_number,
        warnings,
        "account_summary.account_number",
        "account number",
        WarningSeverity.INFO,
    )
    _require(
        s.service_address,
        warnings,
        "account_summary.service_address",
        "service address",
        WarningSeverity.INFO,
    )
    return s


# --------------------------------------------------------------------------------------
# Meter table row parsing (real CoServ bill layout)
# --------------------------------------------------------------------------------------

# Data row format: {meter#}  {from_date}  {to_date}  {days}  {last}  {current}  {mult}  {usage}
# The header row carries column labels ("From", "To", "Days", etc.) — those are skipped.
_METER_ROW_RE = re.compile(
    r"(?im)^\s*(\S+)"                          # meter number
    r"\s+(\d{1,2}/\d{1,2}/\d{4})"             # from date
    r"\s+(\d{1,2}/\d{1,2}/\d{4})"             # to date
    r"\s+(\d{1,3})"                            # billing days
    r"\s+([\d,]+)"                             # last read
    r"\s+([\d,]+)"                             # current read
    r"\s+(\d+(?:\.\d+)?)"                      # multiplier (well-formed int/decimal)
    r"\s+([\d,]+)\s*$"                         # usage (kWh or CCF)
)


def _parse_meter_table_row(region: str) -> Optional[dict]:
    """Parse the tabular data row that carries meter/period/reading/usage in one line.

    Returns a dict with all extracted fields plus ``usage``, or ``None`` if no row is
    found (e.g. the synthetic-fixture format that uses separate labeled lines).
    """
    m = _METER_ROW_RE.search(region)
    if not m:
        return None
    return {
        "meter_number": m.group(1),
        "service_start_date": parse_date(m.group(2)),
        "service_end_date": parse_date(m.group(3)),
        "billing_days": parse_int(m.group(4)),
        "last_read": parse_int(m.group(5)),
        "current_read": parse_int(m.group(6)),
        "multiplier": parse_decimal(m.group(7)),
        "usage": parse_usage(m.group(8)),
    }


# --------------------------------------------------------------------------------------
# Service detail (electric / gas)
# --------------------------------------------------------------------------------------


def _parse_service_common(region: str) -> Tuple[dict, Optional[object]]:
    """Extract shared service fields.

    Returns ``(fields_dict, table_usage)``.  ``table_usage`` is the usage value from the
    tabular data row when the tabular format is detected; ``None`` otherwise (the caller
    should then use ``_find_usage``).
    """
    data: dict = {}
    table_usage = None

    # "Electric Service Rate: Standard Residential" or "Rate: RES - ..."
    data["service_rate"] = (
        _group(region, r"(?i)\brate\s*[:#]\s*([^\n]+)")
        or _group(region, r"(?im)^\s*rate\b\s*[:#]?\s*(.+?)\s*$")
    )

    # Try the tabular row first (real bill format).
    table = _parse_meter_table_row(region)
    if table:
        table_usage = table.pop("usage")
        data.update(table)
        return data, table_usage

    # Fallback: individual labeled-line regexes (synthetic fixture / alternate formats).
    meter = _group(
        region,
        # Require the value to look like an actual meter identifier (digit or alpha-digit,
        # length ≥ 4).  "From" is a column-header word that would otherwise be captured.
        r"(?i)meter\s*(?:number|no\.?|#)\s*[:#]?\s*([A-Za-z]\d{3,}|\d{5,}|[A-Za-z0-9]{4,}(?:-[A-Za-z0-9]+)*)",
    )
    data["meter_number"] = meter

    period = _search(
        region,
        rf"(?i)service\s*period\s*[:#]?\s*({_DATE})\s*(?:to|through|thru|until|\-|–|—)\s*({_DATE})",
    )
    if period:
        data["service_start_date"] = parse_date(period.group(1))
        data["service_end_date"] = parse_date(period.group(2))
    else:
        data["service_start_date"] = parse_date(
            _group(region, rf"(?i)service\s*(?:start|begin|from)\s*(?:date)?\s*[:#]?\s*({_DATE})")
        )
        data["service_end_date"] = parse_date(
            _group(region, rf"(?i)service\s*(?:end|to|through)\s*(?:date)?\s*[:#]?\s*({_DATE})")
        )

    days = _group(region, r"(?i)(?:billing\s*days|number\s*of\s*days|days\s*billed)\s*[:#]?\s*(\d+)")
    if days is None:
        days = _group(region, r"\(?\s*(\d{1,3})\s*days?\b")
    data["billing_days"] = parse_int(days)

    data["last_read"] = parse_int(
        _group(region, r"(?i)(?:previous|prior|last|beginning|begin)\s*read(?:ing)?\s*[:#]?\s*([\d,]+)")
    )
    data["current_read"] = parse_int(
        _group(region, r"(?i)(?:present|current|ending|end)\s*read(?:ing)?\s*[:#]?\s*([\d,]+)")
    )
    data["multiplier"] = parse_decimal(
        _group(region, r"(?i)multiplier\s*[:#]?\s*([\d,]*\.?\d+)")
    )
    return data, None


def _find_usage(region: str, unit_pat: str):
    """Find a usage value (kWh or CCF) in a service-section text region.

    All whitespace anchors are restricted to horizontal space (``[ \\t]*``) to prevent
    the regex from crossing a line boundary and accidentally capturing the meter number
    that follows the column-header "kWh" / "CCF" on the next line.
    """
    for rx in (
        # "kWh Used: 1725" / "Total CCF 36" / "kWh Usage: ..." style
        rf"(?i)(?:total[ \t]+)?{unit_pat}[ \t]*(?:used|usage|consumed|billed)?[ \t]*[:#]?[ \t]*(-?\d[\d,]*(?:\.\d+)?)",
        # "1,725 kWh" inline style
        rf"(?i)(-?\d[\d,]*(?:\.\d+)?)[ \t]*{unit_pat}\b",
    ):
        m = re.search(rx, region)
        if m:
            return parse_usage(m.group(1))
    return None


def _charge_detail(region: str, label: str) -> Optional[dict]:
    """Extract ``{amount, quantity, unit, rate}`` for one charge line.

    Handles all three CoServ charge-line shapes::

        Customer Charge $15.00                         -> amount only
        Energy Charge 1,725 kWh $212.66                -> quantity + unit + amount
        PCRF 1,725 kWh @ -$0.006 $10.35 CR             -> quantity + unit + rate + credit

    The label may be preceded on the line by bar-chart axis numbers (e.g.
    ``"2100 75 PCRF ..."``), so the pattern does NOT anchor the label to the start of the
    line.  The charge ``amount`` is read as the LAST dollar token on the line; any rate
    sits between ``@`` and that amount.  Bar-chart axis numbers are never interpreted as
    data — they are only skipped over.
    """
    lab = _label_to_pattern(label)
    rx = re.compile(
        rf"(?im)^[^\n]*?\b{lab}\b[ \t]*"
        rf"(?:([\d,]+(?:\.\d+)?)[ \t]*(kwh|ccf)\b)?"   # optional quantity + unit
        rf"[ \t]*(?:@[ \t]*(-?\$?[\d.,]+))?"           # optional "@ rate"
        rf"[^\n]*?({_DOLLAR})[ \t]*$"                   # amount = last dollar on the line
    )
    m = rx.search(region)
    if not m:
        return None
    rate = parse_decimal(m.group(3)) if m.group(3) else None
    if rate is not None and m.group(3).strip().lstrip("$").startswith("-"):
        rate = -abs(rate)  # parse_decimal drops a sign that sits before the "$"
    unit = None
    if m.group(2):
        unit = "kWh" if m.group(2).lower() == "kwh" else "CCF"
    return {
        "amount": normalize_money(m.group(4)),
        "quantity": parse_decimal(m.group(1)) if m.group(1) else None,
        "unit": unit,
        "rate": rate,
    }


def _charges_map(
    region: str, labels: List[str], warnings: List[CoservParserWarning], service: str
) -> dict:
    """Return ``{label: detail}`` for every charge label found in ``region``.

    Missing labels are recorded as INFO warnings and omitted from the map.
    """
    found: dict = {}
    for label in labels:
        detail = _charge_detail(region, label)
        if detail is None or detail["amount"] is None:
            _warn(
                warnings,
                "missing_line_item",
                f"{service}: line item '{label}' not found.",
                f"{service}_service.line_items",
                WarningSeverity.INFO,
            )
            continue
        found[label] = detail
    return found


def _line_items_from(charges: dict) -> List[CoservChargeLineItem]:
    return [
        CoservChargeLineItem(
            name=label,
            amount=d["amount"],
            quantity=d["quantity"],
            unit=d["unit"],
            rate=d["rate"],
        )
        for label, d in charges.items()
    ]


# A single charge line: optional leading bar-chart axis integers, a text label (no
# digits), optional "<qty> <unit> @ <rate>", and a trailing dollar amount at end of line.
# This captures EVERY charge (including month-specific riders like "Gas Rate Case Expense",
# "Weather Norm Adj.-Res", or one-time "Initial Gas Meter Set"), so line items always
# reconcile to the section total regardless of which riders a given month carries.
_CHARGE_LINE_RE = re.compile(
    r"(?i)^[ \t]*(?:\d[\d,]*[ \t]+)*"
    r"(?P<label>[A-Za-z][A-Za-z .,&/'\-]*[A-Za-z.])"
    r"(?:[ \t]+(?P<qty>\d[\d,]*(?:\.\d+)?)[ \t]*(?P<unit>kwh|ccf)\b)?"
    r"[ \t]*(?:@[ \t]*(?P<rate>-?\$?[\d.,]+))?"
    r"[^\n]*?"
    r"(?P<amount>\(?\s*-?\s*\$\s*\d[\d,]*(?:\.\d{1,2})?\s*\)?(?:\s*(?:CR|DR))?)[ \t]*$"
)

# Header/label lines inside the charges block that are not actual charges.
_NON_CHARGE_LABELS = {"current charges", "total current charges", "average cost per day"}


def _all_line_items(region: str) -> List[CoservChargeLineItem]:
    """Capture every charge line in the section's Current-Charges block.

    Unlike the fixed-label scalar extraction, this reads whatever line items a given
    month actually contains, so they sum to the section total on every bill.
    """
    end = re.search(r"(?i)total\s+current\s+charges", region)
    block = region[: end.start()] if end else region
    items: List[CoservChargeLineItem] = []
    seen: set = set()
    for line in block.splitlines():
        m = _CHARGE_LINE_RE.match(line)
        if not m:
            continue
        label = re.sub(r"\s+", " ", m.group("label")).strip()
        if label.lower() in _NON_CHARGE_LABELS:
            continue
        amount = normalize_money(m.group("amount"))
        if amount is None:
            continue
        unit = m.group("unit")
        unit = ("kWh" if unit.lower() == "kwh" else "CCF") if unit else None
        rate = parse_decimal(m.group("rate")) if m.group("rate") else None
        if rate is not None and m.group("rate").strip().lstrip("$").startswith("-"):
            rate = -abs(rate)  # parse_decimal drops a sign that sits before the "$"
        qty = parse_decimal(m.group("qty")) if m.group("qty") else None
        key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(
            CoservChargeLineItem(name=label, amount=amount, quantity=qty, unit=unit, rate=rate)
        )
    return items


def _amt(charges: dict, label: str):
    d = charges.get(label)
    return d["amount"] if d else None


def _qty(charges: dict, label: str):
    d = charges.get(label)
    return d["quantity"] if d else None


def _rate(charges: dict, label: str):
    d = charges.get(label)
    return d["rate"] if d else None


_ELECTRIC_LINE_LABELS = [
    "Customer Charge",
    "Energy Charge",
    "PCRF",
    "SCRF",
    "Municipal Franchise Fee",
    "Sales Tax",
    "Operation Roundup",
]

_GAS_LINE_LABELS = [
    "Customer Charge",
    "Interim Rate Adjustment",
    "Usage Charge",
    "Purchased Gas Factor",
    "Municipal Franchise Fee",
    "Gross Receipts Tax",
    "Sales Tax",
]


def _parse_electric_service(
    region: Optional[str], warnings: List[CoservParserWarning]
) -> Optional[CoservElectricService]:
    if not region:
        _warn(warnings, "missing_section", "Electric service section not found.", "electric_service")
        return None
    common, table_usage = _parse_service_common(region)
    svc = CoservElectricService(**common)
    svc.kwh_used = table_usage if table_usage is not None else _find_usage(region, r"kwh")
    svc.total_current_charges = _amount_on_line(region, "Total Current Charges")

    charges = _charges_map(region, _ELECTRIC_LINE_LABELS, warnings, "electric")
    svc.line_items = _all_line_items(region)  # complete list (reconciles to total)
    svc.customer_charge = _amt(charges, "Customer Charge")
    svc.energy_charge_kwh = _qty(charges, "Energy Charge")
    svc.energy_charge_amount = _amt(charges, "Energy Charge")
    svc.pcrf_kwh = _qty(charges, "PCRF")
    svc.pcrf_rate = _rate(charges, "PCRF")
    svc.pcrf_credit_amount = _amt(charges, "PCRF")
    svc.scrf_kwh = _qty(charges, "SCRF")
    svc.scrf_rate = _rate(charges, "SCRF")
    svc.scrf_amount = _amt(charges, "SCRF")
    svc.municipal_franchise_fee = _amt(charges, "Municipal Franchise Fee")
    svc.sales_tax = _amt(charges, "Sales Tax")
    svc.operation_roundup = _amt(charges, "Operation Roundup")

    _require(svc.meter_number, warnings, "electric_service.meter_number", "electric meter number")
    _require(svc.kwh_used, warnings, "electric_service.kwh_used", "electric kWh used")
    _require(svc.billing_days, warnings, "electric_service.billing_days", "electric billing days")
    _require(
        svc.total_current_charges,
        warnings,
        "electric_service.total_current_charges",
        "electric total current charges",
    )
    return svc


def _parse_gas_service(
    region: Optional[str], warnings: List[CoservParserWarning]
) -> Optional[CoservGasService]:
    if not region:
        _warn(warnings, "missing_section", "Gas service section not found.", "gas_service")
        return None
    common, table_usage = _parse_service_common(region)
    svc = CoservGasService(**common)
    svc.ccf_used = table_usage if table_usage is not None else _find_usage(region, r"ccf")
    svc.total_current_charges = _amount_on_line(region, "Total Current Charges")

    charges = _charges_map(region, _GAS_LINE_LABELS, warnings, "gas")
    svc.line_items = _all_line_items(region)  # complete list (reconciles to total)
    svc.customer_charge = _amt(charges, "Customer Charge")
    svc.interim_rate_adjustment = _amt(charges, "Interim Rate Adjustment")
    svc.usage_charge_ccf = _qty(charges, "Usage Charge")
    svc.usage_charge_rate = _rate(charges, "Usage Charge")
    svc.usage_charge_amount = _amt(charges, "Usage Charge")
    svc.purchased_gas_factor_ccf = _qty(charges, "Purchased Gas Factor")
    svc.purchased_gas_factor_rate = _rate(charges, "Purchased Gas Factor")
    svc.purchased_gas_factor_amount = _amt(charges, "Purchased Gas Factor")
    svc.municipal_franchise_fee = _amt(charges, "Municipal Franchise Fee")
    svc.gross_receipts_tax = _amt(charges, "Gross Receipts Tax")
    svc.sales_tax = _amt(charges, "Sales Tax")

    _require(svc.meter_number, warnings, "gas_service.meter_number", "gas meter number")
    _require(svc.ccf_used, warnings, "gas_service.ccf_used", "gas CCF used")
    _require(svc.billing_days, warnings, "gas_service.billing_days", "gas billing days")
    _require(
        svc.total_current_charges,
        warnings,
        "gas_service.total_current_charges",
        "gas total current charges",
    )
    return svc


# --------------------------------------------------------------------------------------
# Snapshot
# --------------------------------------------------------------------------------------

# Three consecutive "$X.XX/day" values on one line — the real CoServ bill format where
# the bar chart columns carry text labels (1 Year Ago / Last Month / This Month).
_PER_DAY_RE = re.compile(
    r"\$(\d+\.\d{1,2})/day\s+\$(\d+\.\d{1,2})/day\s+\$(\d+\.\d{1,2})/day",
    re.IGNORECASE,
)


def _parse_snapshot(
    text: str,
    electric_region: Optional[str],
    gas_region: Optional[str],
    warnings: List[CoservParserWarning],
) -> Optional[CoservSnapshot]:
    snap = CoservSnapshot()
    found_any = False

    # Primary: "$X.XX/day $Y.YY/day $Z.ZZ/day" within each service region.
    # The three values are ordered left-to-right: 1 Year Ago, Last Month, This Month.
    if electric_region:
        m = _PER_DAY_RE.search(electric_region)
        if m:
            snap.electric_average_cost_per_day_one_year_ago = Decimal(m.group(1))
            snap.electric_average_cost_per_day_last_month = Decimal(m.group(2))
            snap.electric_average_cost_per_day_this_month = Decimal(m.group(3))
            found_any = True

    if gas_region:
        m = _PER_DAY_RE.search(gas_region)
        if m:
            snap.gas_average_cost_per_day_one_year_ago = Decimal(m.group(1))
            snap.gas_average_cost_per_day_last_month = Decimal(m.group(2))
            snap.gas_average_cost_per_day_this_month = Decimal(m.group(3))
            found_any = True

    # Fallback: labeled-line format ("One Year Ago  $6.80") used by synthetic fixtures
    # and older bill layouts where the snapshot is a dedicated text section.
    if snap.electric_average_cost_per_day_this_month is None:
        e_hdr = re.search(r"(?i)electric\b[^\n]*average\s*cost\s*(?:per|/)\s*day", text)
        g_hdr = re.search(r"(?i)gas\b[^\n]*average\s*cost\s*(?:per|/)\s*day", text)
        if e_hdr:
            e_end = g_hdr.start() if (g_hdr and g_hdr.start() > e_hdr.start()) else len(text)
            block = text[e_hdr.start() : e_end]
            snap.electric_average_cost_per_day_one_year_ago = _amount_on_line(block, "One Year Ago")
            snap.electric_average_cost_per_day_last_month = _amount_on_line(block, "Last Month")
            snap.electric_average_cost_per_day_this_month = _amount_on_line(block, "This Month")
            if snap.electric_average_cost_per_day_this_month is not None:
                found_any = True
        if g_hdr:
            block = text[g_hdr.start() : len(text)]
            snap.gas_average_cost_per_day_one_year_ago = _amount_on_line(block, "One Year Ago")
            snap.gas_average_cost_per_day_last_month = _amount_on_line(block, "Last Month")
            snap.gas_average_cost_per_day_this_month = _amount_on_line(block, "This Month")
            if snap.gas_average_cost_per_day_this_month is not None:
                found_any = True

    if not found_any:
        _warn(
            warnings,
            "missing_section",
            "Snapshot (average cost per day) section not found as text.",
            "snapshot",
            WarningSeverity.INFO,
        )
        return None

    return snap


# --------------------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------------------

_CENT = Decimal("0.01")
_USAGE_TOL = Decimal("0.5")


def _close(a: Optional[Decimal], b: Optional[Decimal], tol: Decimal = _CENT) -> bool:
    if a is None or b is None:
        return False
    return abs(Decimal(a) - Decimal(b)) <= tol


def _validate(
    summary: CoservAccountSummary,
    electric: Optional[CoservElectricService],
    gas: Optional[CoservGasService],
    warnings: List[CoservParserWarning],
) -> None:
    if (
        summary.electric_service_total is not None
        and summary.gas_service_total is not None
        and summary.current_charges_total is not None
    ):
        total = summary.electric_service_total + summary.gas_service_total
        if not _close(total, summary.current_charges_total):
            _warn(
                warnings,
                "validation_mismatch",
                f"electric ({summary.electric_service_total}) + gas "
                f"({summary.gas_service_total}) = {total} != current charges "
                f"({summary.current_charges_total}).",
                "account_summary.current_charges_total",
            )

    if (
        electric is not None
        and electric.total_current_charges is not None
        and summary.electric_service_total is not None
        and not _close(electric.total_current_charges, summary.electric_service_total)
    ):
        _warn(
            warnings,
            "validation_mismatch",
            f"electric total current charges ({electric.total_current_charges}) != "
            f"electric service total ({summary.electric_service_total}).",
            "electric_service.total_current_charges",
        )

    if (
        gas is not None
        and gas.total_current_charges is not None
        and summary.gas_service_total is not None
        and not _close(gas.total_current_charges, summary.gas_service_total)
    ):
        _warn(
            warnings,
            "validation_mismatch",
            f"gas total current charges ({gas.total_current_charges}) != "
            f"gas service total ({summary.gas_service_total}).",
            "gas_service.total_current_charges",
        )

    _validate_meter(electric, "electric", "kwh_used", warnings)
    _validate_meter(gas, "gas", "ccf_used", warnings)

    for svc, name in ((electric, "electric"), (gas, "gas")):
        if svc is None:
            continue
        if svc.billing_days is not None and svc.billing_days <= 0:
            _warn(
                warnings,
                "validation_mismatch",
                f"{name} billing days must be positive (got {svc.billing_days}).",
                f"{name}_service.billing_days",
            )
        if (
            svc.billing_days is not None
            and svc.service_start_date is not None
            and svc.service_end_date is not None
        ):
            span = (svc.service_end_date - svc.service_start_date).days
            if abs(span - svc.billing_days) > 1:
                _warn(
                    warnings,
                    "validation_mismatch",
                    f"{name} billing days ({svc.billing_days}) does not match date span ({span}).",
                    f"{name}_service.billing_days",
                    WarningSeverity.INFO,
                )

    if (
        summary.total_amount_due is not None
        and summary.current_charges_total is not None
        and summary.balance_forward is not None
    ):
        expected = summary.current_charges_total + summary.balance_forward
        if not _close(expected, summary.total_amount_due):
            _warn(
                warnings,
                "possible_special_adjustment",
                f"total amount due ({summary.total_amount_due}) != current charges + "
                f"balance forward ({expected}); a special adjustment may apply.",
                "account_summary.total_amount_due",
            )

    if (
        summary.previous_balance is not None
        and summary.payment_received is not None
        and summary.balance_forward is not None
    ):
        expected = summary.previous_balance + summary.payment_received
        if not _close(expected, summary.balance_forward):
            _warn(
                warnings,
                "validation_mismatch",
                f"previous balance + payments ({expected}) != balance forward "
                f"({summary.balance_forward}).",
                "account_summary.balance_forward",
                WarningSeverity.INFO,
            )


def _validate_meter(
    svc, name: str, usage_field: str, warnings: List[CoservParserWarning]
) -> None:
    if svc is None:
        return
    used = getattr(svc, usage_field)
    if used is None or svc.last_read is None or svc.current_read is None:
        return
    multiplier = svc.multiplier
    if multiplier is None:
        multiplier = Decimal("1")
        _warn(
            warnings,
            "assumed_default",
            f"{name} multiplier not found; assumed 1 for validation.",
            f"{name}_service.multiplier",
            WarningSeverity.INFO,
        )
    computed = (Decimal(svc.current_read) - Decimal(svc.last_read)) * multiplier
    if abs(computed - Decimal(used)) > _USAGE_TOL:
        _warn(
            warnings,
            "validation_mismatch",
            f"{name}: (current {svc.current_read} - last {svc.last_read}) * multiplier "
            f"{multiplier} = {computed} != reported usage {used}.",
            f"{name}_service.{usage_field}",
        )


def _confidence(warnings: List[CoservParserWarning]) -> ParserConfidence:
    severities = {w.severity for w in warnings}
    if WarningSeverity.ERROR.value in severities:
        return ParserConfidence.LOW
    if WarningSeverity.WARNING.value in severities:
        return ParserConfidence.MEDIUM
    return ParserConfidence.HIGH


# --------------------------------------------------------------------------------------
# Text extraction (pdfplumber, optional OCR fallback)
# --------------------------------------------------------------------------------------


def _ocr_fallback(pdf_path: str) -> str:
    """Attempt OCR only when text extraction failed and OCR deps are present."""
    try:
        import pytesseract  # type: ignore
        from pdf2image import convert_from_path  # type: ignore
    except Exception:
        return ""
    try:
        images = convert_from_path(pdf_path)
        return "\n".join(pytesseract.image_to_string(img) for img in images)
    except Exception:
        return ""


def _extract_text(pdf_path: str) -> Tuple[str, str]:
    """Return (text, method). ``method`` is 'text', 'ocr', or 'none'."""
    pages: List[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    text = "\n".join(pages)
    if text.strip():
        return text, "text"
    ocr = _ocr_fallback(pdf_path)
    if ocr.strip():
        return ocr, "ocr"
    return "", "none"


def extract_text(pdf_path: str) -> str:
    """Public helper: extract raw text from a PDF (used by tests / debug)."""
    return _extract_text(pdf_path)[0]


# --------------------------------------------------------------------------------------
# Redaction / debug
# --------------------------------------------------------------------------------------


# Header/brand words that may sit above an address but are NOT a person's name.
_REDACTION_STOPWORDS = {
    "coserv", "co serv", "account summary", "your account summary",
    "electric service", "gas service", "current charges", "message center",
    "total amount due", "balance forward", "po box", "service addr",
}
# A line that is a US street address: "<number> <words/digits>", no $/comma/colon.
_STREET_LINE_RE = re.compile(r"(?m)^[ \t]*\d{1,6}[ \t]+[A-Za-z][A-Za-z0-9.\-]*(?:[ \t]+[A-Za-z0-9.\-]+)*[ \t]*$")
# A line that is "CITY[,] ST 12345[-6789]".
_CITY_STATE_ZIP_RE = re.compile(r"(?m)^[ \t]*[A-Za-z][A-Za-z .'\-]*,?[ \t]+[A-Z]{2}[ \t]+\d{5}(?:-\d{4})?[ \t]*$")
# A line that looks like a person's name (letters/spaces/punct only — no digits).
_NAME_LINE_RE = re.compile(r"^[A-Za-z][A-Za-z .,'\-]+$")


def _name_candidates(text: str) -> set:
    """Recipient name(s): the non-empty alpha line directly above a street-address line.

    CoServ statements print the addressee name immediately above the street address in the
    mailing block and remittance stub; the name itself is unlabeled, so we locate it
    positionally and redact every occurrence.
    """
    cands: set = set()
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if not _STREET_LINE_RE.match(line):
            continue
        j = i - 1
        while j >= 0 and not lines[j].strip():
            j -= 1
        if j < 0:
            continue
        cand = lines[j].strip()
        if (
            _NAME_LINE_RE.match(cand)
            and len(cand) >= 4
            and cand.lower() not in _REDACTION_STOPWORDS
        ):
            cands.add(cand)
    return cands


def redact_text(text: str) -> str:
    """Mask PII for debug output: name, address, account/meter numbers, phones, emails.

    Redaction only affects what is *displayed* (it is never applied before parsing), so it
    errs toward over-masking — leaking a name or address is unacceptable, while masking a
    stray chart-axis number is harmless. No raw account data is ever printed or logged.
    """
    if not text:
        return text
    out = text

    # 1. Recipient name(s) — positional, before generic masking changes the lines.
    for name in sorted(_name_candidates(out), key=len, reverse=True):
        out = out.replace(name, _REDACTED)

    # 2. Labeled account / meter numbers.
    out = re.sub(
        r"(?i)(account\s*(?:number|no\.?|#)\s*[:#]?\s*)([A-Za-z0-9][A-Za-z0-9\- ]{2,})",
        r"\1" + _REDACTED,
        out,
    )
    out = re.sub(
        r"(?i)(meter\s*(?:number|no\.?|#)?\s*[:#]?\s*)([A-Za-z0-9][A-Za-z0-9\-]{3,})",
        r"\1" + _REDACTED,
        out,
    )
    # 3. Labeled service address ("Service Address: ..." / "Service Addr ...").
    out = re.sub(r"(?im)^(\s*service\s+addr(?:ess)?\s*[:#\-]?\s*).+$", r"\1" + _REDACTED, out)
    out = re.sub(r"(?i)(service\s+addr\b\s*[:#\-]?\s*)([^\n]+)", r"\1" + _REDACTED, out)

    # 4. Structural address lines (street line, city/state/ZIP line).
    out = _STREET_LINE_RE.sub(_REDACTED, out)
    out = _CITY_STATE_ZIP_RE.sub(_REDACTED, out)

    # 5. Phones, emails, and long digit runs (barcodes, IDs) not part of an amount.
    out = re.sub(r"\(?\b\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b", _REDACTED, out)
    out = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", _REDACTED, out)
    out = re.sub(r"(?<![\d.$])\d{6,}(?![\d.])", _REDACTED, out)
    return out


def _redacted_debug_blocks(text: str) -> str:
    """Render redacted text grouped into labeled blocks for ``--debug`` output."""
    redacted = redact_text(text)
    electric_region, gas_region = _slice_sections(redacted)
    snap_pos = _snapshot_start(redacted)
    elec_pos = _find_detail_header(redacted, "electric")
    summary_end = elec_pos if elec_pos is not None else (snap_pos or len(redacted))
    blocks = [
        ("ACCOUNT SUMMARY (redacted)", redacted[:summary_end]),
        ("ELECTRIC SERVICE (redacted)", electric_region or "<not found>"),
        ("GAS SERVICE (redacted)", gas_region or "<not found>"),
        (
            "SNAPSHOT (redacted)",
            redacted[snap_pos:] if snap_pos is not None else "<not found>",
        ),
    ]
    lines = ["=== CoServ parser debug: REDACTED extracted text blocks ==="]
    for title, body in blocks:
        lines.append(f"\n----- {title} -----")
        lines.append(body.strip() or "<empty>")
    return "\n".join(lines)


def redacted_debug_blocks(pdf_path: str) -> str:
    """Public helper: extract a PDF's text and return REDACTED, block-labeled text.

    Used by the API's debug endpoint so the UI can show what was parsed without ever
    exposing raw PII or raw PDF text.
    """
    return _redacted_debug_blocks(extract_text(pdf_path))


# --------------------------------------------------------------------------------------
# Top-level parse
# --------------------------------------------------------------------------------------


def _parse_bill_text(text: str, source_file: Optional[str] = None) -> CoservBill:
    """Parse already-extracted text into a :class:`CoservBill` (pure function)."""
    warnings: List[CoservParserWarning] = []

    if not text or not text.strip():
        _warn(
            warnings,
            "extraction_failed",
            "No text could be extracted from the PDF (OCR unavailable or empty).",
            None,
            WarningSeverity.ERROR,
        )
        return CoservBill(
            account_summary=CoservAccountSummary(),
            parser_confidence=ParserConfidence.LOW,
            warnings=warnings,
            source_file=source_file,
            parser_version=PARSER_VERSION,
        )

    electric_region, gas_region = _slice_sections(text)

    summary = _parse_account_summary(text, warnings)
    electric = _parse_electric_service(electric_region, warnings)
    gas = _parse_gas_service(gas_region, warnings)
    snapshot = _parse_snapshot(text, electric_region, gas_region, warnings)

    _validate(summary, electric, gas, warnings)

    return CoservBill(
        account_summary=summary,
        electric_service=electric,
        gas_service=gas,
        snapshot=snapshot,
        parser_confidence=_confidence(warnings),
        warnings=warnings,
        source_file=source_file,
        parser_version=PARSER_VERSION,
    )


def parse_coserv_bill(pdf_path: str, *, debug: bool = False) -> CoservBill:
    """Parse a CoServ utility bill PDF into a validated :class:`CoservBill`.

    Args:
        pdf_path: Filesystem path to the CoServ bill PDF.
        debug: When ``True``, redacted extracted-text blocks are written to stderr.

    Returns:
        A :class:`CoservBill`. Even when fields are missing the object is returned with
        warnings populated and ``parser_confidence`` lowered, rather than raising.
    """
    path = Path(pdf_path)
    if not path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    text, _method = _extract_text(str(path))
    if debug:
        sys.stderr.write(_redacted_debug_blocks(text) + "\n")
    return _parse_bill_text(text, source_file=path.name)


def parse_coserv_bill_result(pdf_path: str, *, debug: bool = False) -> dict:
    """Standard parser result envelope.

    Returns ``{"success", "bill", "warnings", "parser_version", "parser_confidence"}``.
    ``success`` is False only when extraction itself failed (an error-severity warning);
    a parse with missing fields still succeeds (with warnings + lowered confidence).
    """
    bill = parse_coserv_bill(pdf_path, debug=debug)
    return {
        "success": not bill.errors(),
        "bill": bill.model_dump(mode="json"),
        "warnings": [w.model_dump(mode="json") for w in bill.warnings],
        "parser_version": bill.parser_version,
        "parser_confidence": bill.parser_confidence.value,
    }


def bill_to_json(bill: CoservBill, indent: Optional[int] = 2) -> str:
    """Serialize a parsed bill to a JSON string (Decimals as strings, dates ISO)."""
    return json.dumps(bill.model_dump(mode="json"), indent=indent, sort_keys=False)


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Parse a CoServ utility bill PDF into JSON (deterministic, no LLM)."
    )
    ap.add_argument("pdf_path", help="Path to the CoServ bill PDF")
    ap.add_argument(
        "--debug",
        action="store_true",
        help="Print REDACTED extracted text blocks to stderr (never raw PII).",
    )
    args = ap.parse_args(argv)

    bill = parse_coserv_bill(args.pdf_path, debug=args.debug)
    print(bill_to_json(bill))
    return 0 if bill.parser_confidence != ParserConfidence.LOW else 2


if __name__ == "__main__":
    raise SystemExit(main())
