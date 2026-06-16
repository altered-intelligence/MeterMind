"""Tests against sanitized text in the REAL CoServ bill layout.

Complements test_coserv_parser.py (which uses synthesized PDFs in a simpler layout).
These exercise the meter-table row, charge lines with quantity/rate, the "$/day"
snapshot row, and the granular charge fields. All PII here is fake.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.parsers.coserv_pdf_parser import _parse_bill_text, redact_text
from app.schemas import ParserConfidence, compute_derived
from app.tests.sample_bill_text import SAMPLE_BILL_TEXT, SAMPLE_PII


def _bill():
    return _parse_bill_text(SAMPLE_BILL_TEXT, source_file="sanitized.pdf")


def test_real_layout_account_summary():
    s = _bill().account_summary
    assert s.bill_date == date(2026, 6, 5)
    assert s.due_date == date(2026, 6, 22)
    assert s.previous_balance == Decimal("274.00")
    assert s.payment_received == Decimal("-274.00")  # "$274.00 CR"
    assert s.balance_forward == Decimal("0.00")
    assert s.electric_service_total == Decimal("239.14")
    assert s.gas_service_total == Decimal("77.86")
    assert s.current_charges_total == Decimal("317.00")
    assert s.total_amount_due == Decimal("317.00")
    assert s.amount_due_after_due_date == Decimal("328.25")
    assert s.account_number == SAMPLE_PII["account_number"]
    assert s.service_address == "123 SAMPLE ROAD"


def test_real_layout_meter_table_row():
    e = _bill().electric_service
    assert e.service_rate == "Standard Residential"
    assert e.meter_number == "100200300"
    assert e.service_start_date == date(2026, 4, 30)
    assert e.service_end_date == date(2026, 6, 1)
    assert e.billing_days == 32
    assert e.last_read == 47152
    assert e.current_read == 48877
    assert e.multiplier == Decimal("1")
    assert e.kwh_used == 1725
    # (current - last) * multiplier == usage
    assert (e.current_read - e.last_read) * e.multiplier == e.kwh_used


def test_real_layout_electric_granular_charges():
    e = _bill().electric_service
    assert e.customer_charge == Decimal("15.00")
    assert e.energy_charge_kwh == Decimal("1725")
    assert e.energy_charge_amount == Decimal("212.66")
    assert e.pcrf_kwh == Decimal("1725")
    assert e.pcrf_rate == Decimal("-0.006")
    assert e.pcrf_credit_amount == Decimal("-10.35")  # credit -> negative
    assert e.scrf_kwh == Decimal("1725")
    assert e.scrf_rate == Decimal("0.0045")
    assert e.scrf_amount == Decimal("7.76")
    assert e.municipal_franchise_fee == Decimal("9.94")
    assert e.sales_tax == Decimal("3.40")
    assert e.operation_roundup == Decimal("0.73")
    assert e.total_current_charges == Decimal("239.14")
    # Granular charges reconcile to the section total.
    parts = [
        e.customer_charge, e.energy_charge_amount, e.pcrf_credit_amount,
        e.scrf_amount, e.municipal_franchise_fee, e.sales_tax, e.operation_roundup,
    ]
    assert sum(parts) == e.total_current_charges


def test_real_layout_gas_granular_charges():
    g = _bill().gas_service
    assert g.meter_number == "400500600"
    assert g.ccf_used == 36
    assert g.customer_charge == Decimal("18.00")
    assert g.interim_rate_adjustment == Decimal("3.33")
    assert g.usage_charge_ccf == Decimal("36")
    assert g.usage_charge_rate == Decimal("0.1401")
    assert g.usage_charge_amount == Decimal("5.04")
    assert g.purchased_gas_factor_ccf == Decimal("36")
    assert g.purchased_gas_factor_rate == Decimal("1.25")
    assert g.purchased_gas_factor_amount == Decimal("45.00")
    assert g.municipal_franchise_fee == Decimal("3.84")
    assert g.gross_receipts_tax == Decimal("1.53")
    assert g.sales_tax == Decimal("1.12")
    assert g.total_current_charges == Decimal("77.86")
    parts = [
        g.customer_charge, g.interim_rate_adjustment, g.usage_charge_amount,
        g.purchased_gas_factor_amount, g.municipal_franchise_fee,
        g.gross_receipts_tax, g.sales_tax,
    ]
    assert sum(parts) == g.total_current_charges


def test_real_layout_snapshot_per_day():
    snap = _bill().snapshot
    assert snap.electric_average_cost_per_day_one_year_ago == Decimal("8.11")
    assert snap.electric_average_cost_per_day_last_month == Decimal("6.76")
    assert snap.electric_average_cost_per_day_this_month == Decimal("7.47")
    assert snap.gas_average_cost_per_day_one_year_ago == Decimal("1.69")
    assert snap.gas_average_cost_per_day_last_month == Decimal("2.37")
    assert snap.gas_average_cost_per_day_this_month == Decimal("2.43")


def test_real_layout_confidence_high():
    bill = _bill()
    assert bill.parser_confidence == ParserConfidence.HIGH
    assert bill.errors() == []


def test_real_layout_derived_calculations():
    d = compute_derived(_bill())
    assert d.electric_avg_kwh_per_day == Decimal("53.91")   # 1725 / 32
    assert d.gas_avg_ccf_per_day == Decimal("1.12")          # 36 / 32
    assert d.effective_electric_rate == Decimal("0.1386")    # 239.14 / 1725
    assert d.effective_gas_rate == Decimal("2.1628")         # 77.86 / 36
    assert d.total_avg_cost_per_day == Decimal("9.91")       # 317.00 / 32
    assert d.electric_percentage_of_bill == Decimal("0.7544")
    assert d.gas_percentage_of_bill == Decimal("0.2456")


def test_real_layout_redaction_removes_all_pii():
    redacted = redact_text(SAMPLE_BILL_TEXT)
    for value in SAMPLE_PII.values():
        assert value not in redacted, value
    assert "JANE" not in redacted and "DOE" not in redacted
    assert "[REDACTED]" in redacted
    # Non-PII financial data is preserved for debugging.
    assert "239.14" in redacted
    assert "Energy Charge" in redacted
