"""Schema-contract tests for the CoServ parser.

These pin the *output contract*: whatever the parser returns must be a valid ``CoservBill``
(so a downstream schema-version bump is a deliberate, test-breaking event), and every
required field must be either present or accompanied by a warning naming it — the parser
never silently drops a required field.
"""

from __future__ import annotations

from app.parsers.coserv_pdf_parser import (
    SCHEMA_VERSION,
    _parse_bill_text,
    parse_coserv_bill,
)
from app.schemas import CoservBill, ParserConfidence

# Required fields -> the warning ``field`` the parser emits when they are missing.
_REQUIRED = {
    "account_summary.bill_date": lambda b: b.account_summary.bill_date,
    "account_summary.due_date": lambda b: b.account_summary.due_date,
    "account_summary.total_amount_due": lambda b: b.account_summary.total_amount_due,
    "electric_service.kwh_used": lambda b: b.electric_service and b.electric_service.kwh_used,
    "electric_service.billing_days": lambda b: b.electric_service and b.electric_service.billing_days,
    "gas_service.ccf_used": lambda b: b.gas_service and b.gas_service.ccf_used,
    "gas_service.billing_days": lambda b: b.gas_service and b.gas_service.billing_days,
}


def test_output_is_valid_coservbill_schema(coserv_pdf):
    bill = parse_coserv_bill(coserv_pdf)
    assert isinstance(bill, CoservBill)
    # Round-trip through the schema: dump -> validate must succeed unchanged.
    dumped = bill.model_dump(mode="json")
    revalidated = CoservBill.model_validate(dumped)
    assert revalidated.model_dump(mode="json") == dumped
    assert SCHEMA_VERSION == "coserv-bill-schema-v1"


def test_canonical_bill_has_required_fields_and_high_confidence(coserv_pdf):
    bill = parse_coserv_bill(coserv_pdf)
    assert bill.parser_confidence == ParserConfidence.HIGH
    assert bill.errors() == []  # no error-severity warnings
    for path, getter in _REQUIRED.items():
        assert getter(bill) is not None, f"expected {path} to be present on the canonical bill"


def test_required_fields_present_or_warned_on_partial_bill():
    """A sparse bill (most fields missing) must surface a warning for each missing required
    field rather than dropping it silently."""
    bill = _parse_bill_text("CoServ\nAccount Number: 1234567890\nTotal Amount Due  $50.00\n")
    warned_fields = {w.field for w in bill.warnings if w.field}
    for path, getter in _REQUIRED.items():
        present = getter(bill) is not None
        # Satisfied if the field is present, its exact path is warned, OR its whole section
        # is flagged missing (a coarser but still explicit "not silently dropped" signal).
        section = path.split(".")[0]
        covered = present or path in warned_fields or section in warned_fields
        assert covered, f"required field {path} is neither present nor warned about"
    # A degraded parse must not claim high confidence.
    assert bill.parser_confidence != ParserConfidence.HIGH


def test_empty_text_yields_error_warning_not_exception():
    bill = _parse_bill_text("")
    assert isinstance(bill, CoservBill)
    assert bill.errors(), "empty extraction should produce an error-severity warning"
