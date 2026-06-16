"""Pydantic models describing a parsed CoServ combined (electric + gas) utility bill.

These models are intentionally permissive: every extracted field is ``Optional`` so
that a partially-parsed bill can still be represented (with accompanying warnings and a
lowered ``parser_confidence``) instead of raising during construction.

All monetary values are :class:`decimal.Decimal`. Credits are represented as *negative*
Decimals. Dates are :class:`datetime.date`. Usage values are ``int`` when the source PDF
reports a whole number and ``Decimal`` only when the PDF actually contains a fractional
reading.

Nothing in this module stores raw PDF text — see ``parsers.coserv_pdf_parser`` for the
redaction helpers used by the ``--debug`` mode.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

# A usage reading is normally a whole number (kWh, CCF) but the PDF may occasionally
# report a fractional value. ``int`` is listed first so Pydantic's smart-union keeps an
# integral reading as an ``int`` and only falls back to ``Decimal`` for true fractions.
UsageValue = Union[int, Decimal]


class ParserConfidence(str, Enum):
    """Overall confidence that the extraction reflects the source document."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class WarningSeverity(str, Enum):
    """Severity attached to a :class:`CoservParserWarning`."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class CoservParserWarning(BaseModel):
    """A single structured warning emitted while parsing a bill.

    ``code`` is a stable machine-readable token (e.g. ``"missing_field"`` or
    ``"validation_mismatch"``); ``message`` is human-readable. ``field`` names the model
    field the warning relates to, when applicable.
    """

    model_config = ConfigDict(use_enum_values=True)

    code: str
    message: str
    field: Optional[str] = None
    severity: WarningSeverity = WarningSeverity.WARNING


class CoservChargeLineItem(BaseModel):
    """One charge line within a service section.

    ``amount`` is negative when the line represents a credit. Some charges also carry a
    metered ``quantity`` (kWh/CCF) and a per-unit ``rate`` (e.g. "PCRF 1,725 kWh @
    -$0.006 = $10.35 CR"); these are ``None`` for flat charges. ``currency`` preserves the
    unit of ``amount`` explicitly (always ``"USD"`` for CoServ).
    """

    name: str
    amount: Optional[Decimal] = None
    quantity: Optional[Decimal] = None
    unit: Optional[str] = None
    rate: Optional[Decimal] = None
    currency: str = "USD"


class CoservAccountSummary(BaseModel):
    """The "Account Summary" block from page one of the statement."""

    bill_date: Optional[date] = None
    previous_balance: Optional[Decimal] = None
    payment_received: Optional[Decimal] = None
    balance_forward: Optional[Decimal] = None
    electric_service_total: Optional[Decimal] = None
    gas_service_total: Optional[Decimal] = None
    current_charges_total: Optional[Decimal] = None
    total_amount_due: Optional[Decimal] = None
    due_date: Optional[date] = None
    amount_due_after_due_date: Optional[Decimal] = None
    autopay_status: Optional[str] = None
    account_number: Optional[str] = None
    service_address: Optional[str] = None
    currency: str = "USD"


class CoservServiceBase(BaseModel):
    """Fields shared by the electric and gas service-detail sections."""

    service_rate: Optional[str] = None
    meter_number: Optional[str] = None
    service_start_date: Optional[date] = None
    service_end_date: Optional[date] = None
    billing_days: Optional[int] = None
    last_read: Optional[int] = None
    current_read: Optional[int] = None
    multiplier: Optional[Decimal] = None
    line_items: List[CoservChargeLineItem] = Field(default_factory=list)
    total_current_charges: Optional[Decimal] = None
    currency: str = "USD"

    def line_item(self, name: str) -> Optional[CoservChargeLineItem]:
        """Return the first line item whose name matches ``name`` (case-insensitive)."""
        target = name.strip().lower()
        for item in self.line_items:
            if item.name.strip().lower() == target:
                return item
        return None


class CoservElectricService(CoservServiceBase):
    """Electric service detail, including kWh usage and electric-specific charges.

    ``total_current_charges`` (inherited) is the electric section total — i.e. the
    spec's ``total_current_electric_charges``.
    """

    kwh_used: Optional[UsageValue] = None
    usage_unit: str = "kWh"

    # Granular charge breakdown.
    customer_charge: Optional[Decimal] = None
    energy_charge_kwh: Optional[Decimal] = None
    energy_charge_amount: Optional[Decimal] = None
    pcrf_kwh: Optional[Decimal] = None
    pcrf_rate: Optional[Decimal] = None
    pcrf_credit_amount: Optional[Decimal] = None  # negative when a credit
    scrf_kwh: Optional[Decimal] = None
    scrf_rate: Optional[Decimal] = None
    scrf_amount: Optional[Decimal] = None
    municipal_franchise_fee: Optional[Decimal] = None
    sales_tax: Optional[Decimal] = None
    operation_roundup: Optional[Decimal] = None

    @property
    def total_current_electric_charges(self) -> Optional[Decimal]:
        return self.total_current_charges


class CoservGasService(CoservServiceBase):
    """Gas service detail, including CCF usage and gas-specific charges.

    ``total_current_charges`` (inherited) is the gas section total — i.e. the spec's
    ``total_current_gas_charges``.
    """

    ccf_used: Optional[UsageValue] = None
    usage_unit: str = "CCF"

    # Granular charge breakdown.
    customer_charge: Optional[Decimal] = None
    interim_rate_adjustment: Optional[Decimal] = None
    usage_charge_ccf: Optional[Decimal] = None
    usage_charge_rate: Optional[Decimal] = None
    usage_charge_amount: Optional[Decimal] = None
    purchased_gas_factor_ccf: Optional[Decimal] = None
    purchased_gas_factor_rate: Optional[Decimal] = None
    purchased_gas_factor_amount: Optional[Decimal] = None
    municipal_franchise_fee: Optional[Decimal] = None
    gross_receipts_tax: Optional[Decimal] = None
    sales_tax: Optional[Decimal] = None

    @property
    def total_current_gas_charges(self) -> Optional[Decimal]:
        return self.total_current_charges


class CoservSnapshot(BaseModel):
    """"Average cost per day" comparison snapshot (text values only).

    Bar-chart geometry is never parsed; only values that appear as text are captured.
    """

    electric_average_cost_per_day_one_year_ago: Optional[Decimal] = None
    electric_average_cost_per_day_last_month: Optional[Decimal] = None
    electric_average_cost_per_day_this_month: Optional[Decimal] = None
    gas_average_cost_per_day_one_year_ago: Optional[Decimal] = None
    gas_average_cost_per_day_last_month: Optional[Decimal] = None
    gas_average_cost_per_day_this_month: Optional[Decimal] = None
    currency: str = "USD"


class CoservBill(BaseModel):
    """A fully parsed CoServ statement.

    ``account_summary`` is always present (possibly with ``None`` fields and warnings).
    The service and snapshot sections are ``None`` only when the corresponding section
    could not be located at all.
    """

    account_summary: CoservAccountSummary = Field(default_factory=CoservAccountSummary)
    electric_service: Optional[CoservElectricService] = None
    gas_service: Optional[CoservGasService] = None
    snapshot: Optional[CoservSnapshot] = None
    parser_confidence: ParserConfidence = ParserConfidence.LOW
    warnings: List[CoservParserWarning] = Field(default_factory=list)
    # Basename only — never the full path, and never raw extracted text.
    source_file: Optional[str] = None
    parser_version: str = "coserv-v0.1.0"

    def errors(self) -> List[CoservParserWarning]:
        """Return only the error-severity warnings."""
        return [w for w in self.warnings if w.severity == WarningSeverity.ERROR.value]


class CoservBillDerived(BaseModel):
    """Analytics derived from a parsed bill (not parsed from the PDF).

    Every value is ``None`` when its inputs are missing or would divide by zero, so the
    dashboard can render gaps without raising.
    """

    electric_avg_kwh_per_day: Optional[Decimal] = None
    gas_avg_ccf_per_day: Optional[Decimal] = None
    effective_electric_rate: Optional[Decimal] = None  # $/kWh
    effective_gas_rate: Optional[Decimal] = None        # $/CCF
    electric_avg_cost_per_day: Optional[Decimal] = None  # electric $ / billing day
    gas_avg_cost_per_day: Optional[Decimal] = None        # gas $ / billing day
    total_avg_cost_per_day: Optional[Decimal] = None
    electric_percentage_of_bill: Optional[Decimal] = None  # 0..1
    gas_percentage_of_bill: Optional[Decimal] = None       # 0..1


def _safe_div(numerator, denominator, places: str = "0.0001") -> Optional[Decimal]:
    """Decimal division guarding against ``None`` and zero denominators."""
    if numerator is None or denominator is None:
        return None
    num, den = Decimal(numerator), Decimal(denominator)
    if den == 0:
        return None
    return (num / den).quantize(Decimal(places))


def compute_derived(bill: "CoservBill") -> CoservBillDerived:
    """Compute the spec's derived analytics from a parsed bill."""
    s = bill.account_summary
    e = bill.electric_service
    g = bill.gas_service

    d = CoservBillDerived()
    if e is not None:
        d.electric_avg_kwh_per_day = _safe_div(e.kwh_used, e.billing_days, "0.01")
        d.effective_electric_rate = _safe_div(e.total_current_charges, e.kwh_used, "0.0001")
        d.electric_avg_cost_per_day = _safe_div(s.electric_service_total, e.billing_days, "0.01")
    if g is not None:
        d.gas_avg_ccf_per_day = _safe_div(g.ccf_used, g.billing_days, "0.01")
        d.effective_gas_rate = _safe_div(g.total_current_charges, g.ccf_used, "0.0001")
        d.gas_avg_cost_per_day = _safe_div(s.gas_service_total, g.billing_days, "0.01")

    # total_avg_cost_per_day uses the billing-cycle length (electric and gas share it).
    billing_days = None
    if e is not None and e.billing_days:
        billing_days = e.billing_days
    elif g is not None and g.billing_days:
        billing_days = g.billing_days
    d.total_avg_cost_per_day = _safe_div(s.total_amount_due, billing_days, "0.01")

    d.electric_percentage_of_bill = _safe_div(
        s.electric_service_total, s.current_charges_total, "0.0001"
    )
    d.gas_percentage_of_bill = _safe_div(
        s.gas_service_total, s.current_charges_total, "0.0001"
    )
    return d


# ======================================================================================
# Bill audit (deterministic internal-math validation)
# ======================================================================================


class BillAuditStatus(str, Enum):
    """Outcome of a single check (or, excluding ``skipped``, of the whole audit)."""

    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"
    SKIPPED = "skipped"


class BillAuditSeverity(str, Enum):
    """How important a check is (independent of its pass/fail outcome)."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class BillAuditCheck(BaseModel):
    """One internal-consistency check. Values are precision-preserving strings."""

    model_config = ConfigDict(use_enum_values=True)

    check_code: str
    label: str
    group: str  # account_summary | electric | gas | snapshot
    status: BillAuditStatus
    severity: BillAuditSeverity
    expected_value: Optional[str] = None
    actual_value: Optional[str] = None
    difference: Optional[str] = None
    tolerance: Optional[str] = None
    message: str = ""
    related_fields: List[str] = Field(default_factory=list)


class BillAuditResult(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    bill_id: Optional[int] = None
    audit_version: str
    overall_status: BillAuditStatus  # pass | warning | fail
    checks: List[BillAuditCheck] = Field(default_factory=list)
    created_at: Optional[datetime] = None


# --------------------------------------------------------------------------------------
# Rate & fee breakdown
# --------------------------------------------------------------------------------------


class RateBreakdownCategory(BaseModel):
    """One spend category within a service section.

    ``amount`` is the signed sum of the category's line items (credits negative).
    ``percentage_of_service_total`` is ``amount / total_current_charges`` expressed as a
    fraction in ``[..]`` (e.g. ``"0.42"`` = 42%), or ``None`` when the service total is
    zero/unknown. Money is a precision-preserving string; nothing here is a float.
    """

    key: str  # e.g. "fixed_charges", "variable_energy_charges", "total", "uncategorized"
    label: str  # human-readable, e.g. "Fixed charges"
    amount: Optional[str] = None
    percentage_of_service_total: Optional[str] = None
    line_item_names: List[str] = Field(default_factory=list)


class ServiceRateBreakdown(BaseModel):
    """Categorized breakdown plus derived rate metrics for one service section."""

    service_type: str  # "electric" | "gas"
    total_current_charges: Optional[str] = None
    total_categorized: Optional[str] = None  # sum of all non-``total`` categories
    reconciles: bool = False  # |total_categorized - total_current_charges| <= $0.01
    categories: List[RateBreakdownCategory] = Field(default_factory=list)
    # Precision-preserving string metrics (rates to 4dp, percentages as fractions):
    metrics: dict[str, Optional[str]] = Field(default_factory=dict)
    uncategorized_labels: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class RateBreakdown(BaseModel):
    """Full rate & fee breakdown for a bill (electric and/or gas)."""

    bill_id: Optional[int] = None
    breakdown_version: str
    electric: Optional[ServiceRateBreakdown] = None
    gas: Optional[ServiceRateBreakdown] = None
    generated_at: Optional[datetime] = None


# --------------------------------------------------------------------------------------
# Bulk upload / import batches
# --------------------------------------------------------------------------------------


class ImportItemStatus(str, Enum):
    """Outcome for a single file in a bulk import."""

    IMPORTED = "imported"   # newly stored, no warnings
    WARNING = "warning"     # stored, but the parser emitted warnings
    DUPLICATE = "duplicate"  # an equivalent bill already exists; nothing stored
    FAILED = "failed"       # could not parse/store (invalid PDF, parser error, ...)


class ImportBatchItemResult(BaseModel):
    """Per-file result, both as the live bulk-upload response and the stored history row."""

    model_config = ConfigDict(use_enum_values=True)

    filename: str
    status: ImportItemStatus
    bill_id: Optional[int] = None
    bill_date: Optional[str] = None
    total_amount_due: Optional[str] = None
    parser_confidence: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None  # human-readable reason for duplicate/failed


class ImportBatchResult(BaseModel):
    """A bulk-import run with its per-file results and roll-up counts."""

    id: int
    status: str  # processing | completed | completed_with_errors
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    total_files: int = 0
    imported_count: int = 0
    duplicate_count: int = 0
    failed_count: int = 0
    warning_count: int = 0
    items: List[ImportBatchItemResult] = Field(default_factory=list)


# --------------------------------------------------------------------------------------
# Manual field corrections
# --------------------------------------------------------------------------------------


class CorrectionRequest(BaseModel):
    """A request to correct one parsed field. ``corrected_value`` is always a string; the
    service validates/parses it against the field's expected type."""

    field_path: str = Field(..., min_length=1)
    corrected_value: str
    reason: Optional[str] = None


class BillFieldCorrectionModel(BaseModel):
    """A stored correction (one entry of a field's audit history)."""

    id: int
    bill_id: int
    field_path: str
    original_value: Optional[str] = None
    corrected_value: Optional[str] = None
    corrected_value_type: Optional[str] = None
    reason: Optional[str] = None
    corrected_by_user_id: Optional[int] = None
    created_at: Optional[datetime] = None


class CorrectionResponse(BaseModel):
    """Result of applying a correction: the new record, the recomputed effective bill, and
    the freshly re-run audit."""

    success: bool = True
    correction: BillFieldCorrectionModel
    updated_bill: dict[str, Any] = Field(default_factory=dict)
    new_audit_result: dict[str, Any] = Field(default_factory=dict)
