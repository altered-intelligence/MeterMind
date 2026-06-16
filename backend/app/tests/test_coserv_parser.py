"""Tests for the deterministic CoServ bill parser.

Inputs are synthetic CoServ-shaped PDFs (see conftest.py); expected values are the
sanitized sample values from the task spec. No real bill or PII is used.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.parsers.coserv_pdf_parser import (
    extract_text,
    normalize_money,
    parse_coserv_bill,
    redact_text,
)
from app.schemas import CoservBill, ParserConfidence


# --------------------------------------------------------------------------------------
# Account summary
# --------------------------------------------------------------------------------------


def test_parse_account_summary(parsed_bill, expected):
    s = parsed_bill.account_summary
    assert s.bill_date == expected["bill_date"]
    assert s.due_date == expected["due_date"]
    assert s.previous_balance == expected["previous_balance"]
    assert s.payment_received == expected["payment_received"]
    assert s.balance_forward == expected["balance_forward"]
    assert s.electric_service_total == expected["electric_service_total"]
    assert s.gas_service_total == expected["gas_service_total"]
    assert s.current_charges_total == expected["current_charges_total"]
    assert s.total_amount_due == expected["total_amount_due"]
    assert s.amount_due_after_due_date == expected["amount_due_after_due_date"]
    assert s.account_number == expected["account_number"]
    assert s.service_address == expected["service_address"]
    assert s.autopay_status == expected["autopay_status"]

    # The clean sample bill should parse without warnings -> high confidence.
    assert parsed_bill.parser_confidence == ParserConfidence.HIGH
    assert parsed_bill.errors() == []


# --------------------------------------------------------------------------------------
# Electric service
# --------------------------------------------------------------------------------------


def test_parse_electric_service(parsed_bill, expected):
    e = parsed_bill.electric_service
    exp = expected["electric"]
    assert e is not None
    assert e.meter_number == exp["meter_number"]
    assert e.service_start_date == exp["service_start_date"]
    assert e.service_end_date == exp["service_end_date"]
    assert e.billing_days == exp["billing_days"]
    assert e.last_read == exp["last_read"]
    assert e.current_read == exp["current_read"]
    assert e.multiplier == exp["multiplier"]
    assert e.kwh_used == exp["kwh_used"]
    assert isinstance(e.kwh_used, int)  # whole reading -> int, not Decimal
    assert e.usage_unit == "kWh"
    assert e.total_current_charges == exp["total_current_charges"]

    parsed_items = {li.name: li.amount for li in e.line_items}
    for name, amount in exp["line_items"].items():
        assert parsed_items.get(name) == amount, name
    # Line items should sum to the section total.
    assert sum(li.amount for li in e.line_items) == exp["total_current_charges"]


# --------------------------------------------------------------------------------------
# Gas service
# --------------------------------------------------------------------------------------


def test_parse_gas_service(parsed_bill, expected):
    g = parsed_bill.gas_service
    exp = expected["gas"]
    assert g is not None
    assert g.meter_number == exp["meter_number"]
    assert g.service_start_date == exp["service_start_date"]
    assert g.service_end_date == exp["service_end_date"]
    assert g.billing_days == exp["billing_days"]
    assert g.last_read == exp["last_read"]
    assert g.current_read == exp["current_read"]
    assert g.multiplier == exp["multiplier"]
    assert g.ccf_used == exp["ccf_used"]
    assert isinstance(g.ccf_used, int)
    assert g.usage_unit == "CCF"
    assert g.total_current_charges == exp["total_current_charges"]

    parsed_items = {li.name: li.amount for li in g.line_items}
    for name, amount in exp["line_items"].items():
        assert parsed_items.get(name) == amount, name
    assert sum(li.amount for li in g.line_items) == exp["total_current_charges"]


# --------------------------------------------------------------------------------------
# Snapshot
# --------------------------------------------------------------------------------------


def test_parse_snapshot_values(parsed_bill, expected):
    snap = parsed_bill.snapshot
    exp = expected["snapshot"]
    assert snap is not None
    assert snap.electric_average_cost_per_day_one_year_ago == exp["electric_one_year_ago"]
    assert snap.electric_average_cost_per_day_last_month == exp["electric_last_month"]
    assert snap.electric_average_cost_per_day_this_month == exp["electric_this_month"]
    assert snap.gas_average_cost_per_day_one_year_ago == exp["gas_one_year_ago"]
    assert snap.gas_average_cost_per_day_last_month == exp["gas_last_month"]
    assert snap.gas_average_cost_per_day_this_month == exp["gas_this_month"]


# --------------------------------------------------------------------------------------
# Credits -> negative Decimals
# --------------------------------------------------------------------------------------


def test_credits_are_negative(parsed_bill):
    # "$274.00 CR" must be stored as a negative Decimal.
    assert parsed_bill.account_summary.payment_received == Decimal("-274.00")
    assert parsed_bill.account_summary.payment_received < 0

    # normalize_money handles the documented credit formats.
    assert normalize_money("$317.00") == Decimal("317.00")
    assert normalize_money("$274.00 CR") == Decimal("-274.00")
    assert normalize_money("$10.35 CR") == Decimal("-10.35")
    assert normalize_money("($10.35)") == Decimal("-10.35")
    assert normalize_money("-$10.35") == Decimal("-10.35")
    assert normalize_money("$1,234.50") == Decimal("1234.50")


# --------------------------------------------------------------------------------------
# Meter-reading validation
# --------------------------------------------------------------------------------------


def test_meter_reading_validation(parsed_bill, make_pdf, primary_lines):
    # Consistent bill: (current - last) * multiplier == usage, and no mismatch warning.
    e = parsed_bill.electric_service
    g = parsed_bill.gas_service
    assert (e.current_read - e.last_read) * e.multiplier == e.kwh_used
    assert (g.current_read - g.last_read) * g.multiplier == g.ccf_used
    assert not any(
        w.code == "validation_mismatch" and "kwh_used" in (w.field or "")
        for w in parsed_bill.warnings
    )

    # Tamper the electric usage so the meter math no longer reconciles.
    tampered = [
        "kWh Used: 9999" if line.startswith("kWh Used:") else line
        for line in primary_lines
    ]
    bill = parse_coserv_bill(make_pdf(tampered))
    assert any(
        w.code == "validation_mismatch" and "kwh_used" in (w.field or "")
        for w in bill.warnings
    )
    # A validation mismatch is a (non-fatal) warning -> medium confidence.
    assert bill.parser_confidence == ParserConfidence.MEDIUM


# --------------------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------------------


def test_redaction(coserv_pdf, expected):
    raw = extract_text(coserv_pdf)
    # Sanity: the raw extracted text *does* contain the sensitive values...
    assert expected["account_number"] in raw

    redacted = redact_text(raw)
    # ...and the redacted text does not.
    assert expected["account_number"] not in redacted
    assert "000123456" not in redacted  # electric meter number
    assert "000654321" not in redacted  # gas meter number
    assert "940-321-7800" not in redacted  # phone number
    assert "[REDACTED]" in redacted
    # The service-address street value should be masked, but the label remains.
    assert "123 Sample Road" not in redacted
    assert "Service Address" in redacted
    # Non-PII figures (amounts/usage) should survive so debugging stays useful.
    assert "317.00" in redacted
    assert "1725" in redacted


# --------------------------------------------------------------------------------------
# JSON serialization
# --------------------------------------------------------------------------------------


def test_json_serialization(parsed_bill, expected):
    data = parsed_bill.model_dump(mode="json")

    # Must be JSON-encodable without custom encoders.
    encoded = json.dumps(data)
    decoded = json.loads(encoded)

    # Money is serialized precisely as strings; dates as ISO-8601; usage as int.
    assert decoded["account_summary"]["total_amount_due"] == "317.00"
    assert decoded["account_summary"]["payment_received"] == "-274.00"
    assert decoded["account_summary"]["bill_date"] == "2026-06-05"
    assert decoded["electric_service"]["kwh_used"] == 1725
    assert isinstance(decoded["electric_service"]["kwh_used"], int)
    assert decoded["parser_confidence"] == "high"

    # Round-trips back into the model with values intact.
    rebuilt = CoservBill.model_validate(decoded)
    assert rebuilt.account_summary.total_amount_due == expected["total_amount_due"]
    assert rebuilt.account_summary.payment_received == expected["payment_received"]
    assert rebuilt.electric_service.kwh_used == expected["electric"]["kwh_used"]
    assert rebuilt.gas_service.ccf_used == expected["gas"]["ccf_used"]


# --------------------------------------------------------------------------------------
# Extra coverage: format-drift robustness (regexes must not match one rigid template)
# --------------------------------------------------------------------------------------


def test_format_drift_variant(coserv_pdf_variant, expected):
    bill = parse_coserv_bill(coserv_pdf_variant)
    s = bill.account_summary
    assert s.bill_date == expected["bill_date"]
    assert s.due_date == expected["due_date"]
    assert s.total_amount_due == expected["total_amount_due"]
    assert s.electric_service_total == expected["electric_service_total"]
    assert s.gas_service_total == expected["gas_service_total"]
    assert s.payment_received == expected["payment_received"]  # "$274.00CR", no space
    assert s.autopay_status == "enrolled"  # "AutoPay is active"

    assert bill.electric_service.kwh_used == expected["electric"]["kwh_used"]
    assert bill.electric_service.billing_days == 32
    assert bill.gas_service.ccf_used == expected["gas"]["ccf_used"]
    assert bill.snapshot.electric_average_cost_per_day_this_month == Decimal("7.47")
    assert bill.snapshot.gas_average_cost_per_day_this_month == Decimal("2.43")
    # Despite the heavy reformatting, the bill still reconciles cleanly.
    assert bill.parser_confidence == ParserConfidence.HIGH


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        parse_coserv_bill("/nonexistent/path/to/bill.pdf")
