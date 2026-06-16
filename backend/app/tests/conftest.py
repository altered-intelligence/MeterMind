"""Pytest fixtures for the CoServ parser tests.

A real CoServ bill contains PII and cannot be committed, so these tests build a
*synthetic* PDF whose extracted-text layout mirrors a real combined electric + gas
statement. All values are sanitized and match the expected values in the task spec.

Two layouts are produced:

* ``coserv_pdf``          — the canonical layout.
* ``coserv_pdf_variant``  — the same bill re-worded/re-spaced (different labels, missing
  colons, alternate separators) to verify the parser's regexes tolerate month-to-month
  formatting drift rather than matching one rigid template.

The expected values live in :data:`EXPECTED`; the rendered line text is defined separately
and on purpose, so a typo in either surfaces as a failing test.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date
from decimal import Decimal
from typing import Callable, List

import pytest
from fpdf import FPDF
from fpdf.enums import XPos, YPos

# Point the app at throwaway storage/DB/key BEFORE any app module reads settings, so the
# API tests never touch the real backend/var/ directory. setdefault respects a real env.
_TEST_TMP = tempfile.mkdtemp(prefix="hui_pytest_")
os.environ.setdefault("HUI_STORAGE_DIR", os.path.join(_TEST_TMP, "bills"))
os.environ.setdefault("HUI_DATABASE_URL", f"sqlite:///{os.path.join(_TEST_TMP, 'test.db')}")
os.environ.setdefault("HUI_SECRET_KEY_FILE", os.path.join(_TEST_TMP, ".secret_key"))

# --------------------------------------------------------------------------------------
# Sanitized expected values (from the task spec; line-item breakdowns sum to the totals).
# --------------------------------------------------------------------------------------

EXPECTED = {
    "bill_date": date(2026, 6, 5),
    "due_date": date(2026, 6, 22),
    "previous_balance": Decimal("274.00"),
    "payment_received": Decimal("-274.00"),  # "$274.00 CR" -> negative
    "balance_forward": Decimal("0.00"),
    "electric_service_total": Decimal("239.14"),
    "gas_service_total": Decimal("77.86"),
    "current_charges_total": Decimal("317.00"),
    "total_amount_due": Decimal("317.00"),
    "amount_due_after_due_date": Decimal("328.25"),
    "account_number": "1234567890",
    "service_address": "123 Sample Road, Aubrey, TX 76227",
    "autopay_status": "enrolled",
    "electric": {
        "meter_number": "000123456",
        "service_start_date": date(2026, 5, 4),
        "service_end_date": date(2026, 6, 5),
        "billing_days": 32,
        "last_read": 45230,
        "current_read": 46955,
        "multiplier": Decimal("1"),
        "kwh_used": 1725,
        "total_current_charges": Decimal("239.14"),
        "line_items": {
            "Customer Charge": Decimal("30.00"),
            "Energy Charge": Decimal("180.00"),
            "PCRF": Decimal("12.00"),
            "SCRF": Decimal("5.00"),
            "Municipal Franchise Fee": Decimal("6.50"),
            "Sales Tax": Decimal("5.39"),
            "Operation Roundup": Decimal("0.25"),
        },
    },
    "gas": {
        "meter_number": "000654321",
        "service_start_date": date(2026, 5, 4),
        "service_end_date": date(2026, 6, 5),
        "billing_days": 32,
        "last_read": 8820,
        "current_read": 8856,
        "multiplier": Decimal("1"),
        "ccf_used": 36,
        "total_current_charges": Decimal("77.86"),
        "line_items": {
            "Customer Charge": Decimal("19.95"),
            "Interim Rate Adjustment": Decimal("1.50"),
            "Usage Charge": Decimal("28.00"),
            "Purchased Gas Factor": Decimal("18.00"),
            "Municipal Franchise Fee": Decimal("2.50"),
            "Gross Receipts Tax": Decimal("1.50"),
            "Sales Tax": Decimal("6.41"),
        },
    },
    "snapshot": {
        "electric_one_year_ago": Decimal("6.80"),
        "electric_last_month": Decimal("5.20"),
        "electric_this_month": Decimal("7.47"),
        "gas_one_year_ago": Decimal("3.10"),
        "gas_last_month": Decimal("4.50"),
        "gas_this_month": Decimal("2.43"),
    },
}

# --------------------------------------------------------------------------------------
# Rendered line text — canonical layout
# --------------------------------------------------------------------------------------

PRIMARY_LINES: List[str] = [
    "CoServ",
    "7701 South Stemmons, Corinth, TX 76210",
    "www.coserv.com    940-321-7800",
    "",
    "Account Number: 1234567890",
    "Service Address: 123 Sample Road, Aubrey, TX 76227",
    "Statement Date: 06/05/2026",
    "",
    "ACCOUNT SUMMARY",
    "Previous Balance  $274.00",
    "Payment(s) Received  $274.00 CR",
    "Balance Forward  $0.00",
    "Electric Service  $239.14",
    "Gas Service  $77.86",
    "Current Charges  $317.00",
    "Total Amount Due  $317.00",
    "Due Date  06/22/2026",
    "Amount Due After 06/22/2026  $328.25",
    "You are enrolled in AutoPay.",
    "",
    "ELECTRIC SERVICE",
    "Rate: RES - Residential Service",
    "Meter Number: 000123456",
    "Service Period: 05/04/2026 to 06/05/2026 (32 Days)",
    "Previous Reading: 45230",
    "Current Reading: 46955",
    "Multiplier: 1",
    "kWh Used: 1725",
    "Customer Charge  $30.00",
    "Energy Charge  $180.00",
    "PCRF  $12.00",
    "SCRF  $5.00",
    "Municipal Franchise Fee  $6.50",
    "Sales Tax  $5.39",
    "Operation Roundup  $0.25",
    "Total Current Charges  $239.14",
    "",
    "GAS SERVICE",
    "Rate: RES - Residential Service",
    "Meter Number: 000654321",
    "Service Period: 05/04/2026 to 06/05/2026 (32 Days)",
    "Previous Reading: 8820",
    "Current Reading: 8856",
    "Multiplier: 1",
    "CCF Used: 36",
    "Customer Charge  $19.95",
    "Interim Rate Adjustment  $1.50",
    "Usage Charge  $28.00",
    "Purchased Gas Factor  $18.00",
    "Municipal Franchise Fee  $2.50",
    "Gross Receipts Tax  $1.50",
    "Sales Tax  $6.41",
    "Total Current Charges  $77.86",
    "",
    "USAGE SNAPSHOT",
    "Electric Average Cost Per Day",
    "One Year Ago  $6.80",
    "Last Month  $5.20",
    "This Month  $7.47",
    "Gas Average Cost Per Day",
    "One Year Ago  $3.10",
    "Last Month  $4.50",
    "This Month  $2.43",
]

# --------------------------------------------------------------------------------------
# Rendered line text — format-drift variant (same numbers, different wording/spacing)
# --------------------------------------------------------------------------------------

VARIANT_LINES: List[str] = [
    "COSERV ELECTRIC & GAS",
    "Account No. 9876543210",
    "Service Address - 500 Example Pkwy, Krugerville, TX 76227",
    "Bill Date 06/05/2026",
    "Account Summary",
    "Previous Balance $274.00",
    "Payments Received $274.00CR",
    "Balance Forward $0.00",
    "Electric Service $239.14",
    "Gas Service $77.86",
    "Current Charges $317.00",
    "Total Amount Due $317.00",
    "Payment Due Date 06/22/2026",
    "Amount Due After 06/22/2026 $328.25",
    "AutoPay is active",
    "Electric Service",
    "Rate RES Residential",
    "Meter No 000111222",
    "Service Period 05/04/2026 - 06/05/2026 32 days",
    "Prior Reading 45230",
    "Current Reading 46955",
    "Multiplier 1",
    "Total kWh 1725",
    "Customer Charge $30.00",
    "Energy Charge $180.00",
    "PCRF $12.00",
    "SCRF $5.00",
    "Municipal Franchise Fee $6.50",
    "Sales Tax $5.39",
    "Operation Roundup $0.25",
    "Total Current Charges $239.14",
    "Gas Service",
    "Rate RES Residential",
    "Meter No 000333444",
    "Service Period 05/04/2026 - 06/05/2026 32 days",
    "Prior Reading 8820",
    "Current Reading 8856",
    "Multiplier 1",
    "Total CCF 36",
    "Customer Charge $19.95",
    "Interim Rate Adjustment $1.50",
    "Usage Charge $28.00",
    "Purchased Gas Factor $18.00",
    "Municipal Franchise Fee $2.50",
    "Gross Receipts Tax $1.50",
    "Sales Tax $6.41",
    "Total Current Charges $77.86",
    "Usage Snapshot",
    "Electric Average Cost / Day",
    "One Year Ago $6.80",
    "Last Month $5.20",
    "This Month $7.47",
    "Gas Average Cost / Day",
    "One Year Ago $3.10",
    "Last Month $4.50",
    "This Month $2.43",
]


# --------------------------------------------------------------------------------------
# PDF rendering
# --------------------------------------------------------------------------------------


def render_pdf(lines: List[str], path) -> str:
    """Render ``lines`` (one per text row) to a real PDF that pdfplumber can read."""
    pdf = FPDF(format="letter", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)
    for line in lines:
        pdf.cell(0, 5, line if line else " ", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.output(str(path))
    return str(path)


# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def expected() -> dict:
    return EXPECTED


@pytest.fixture(scope="session")
def coserv_pdf(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("coserv") / "coserv_bill.pdf"
    return render_pdf(PRIMARY_LINES, path)


@pytest.fixture(scope="session")
def coserv_pdf_variant(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("coserv") / "coserv_bill_variant.pdf"
    return render_pdf(VARIANT_LINES, path)


@pytest.fixture()
def make_pdf(tmp_path) -> Callable[..., str]:
    """Factory: render an arbitrary list of lines to a temp PDF and return its path."""
    counter = {"n": 0}

    def _make(lines: List[str], name: str = None) -> str:
        counter["n"] += 1
        filename = name or f"bill_{counter['n']}.pdf"
        return render_pdf(lines, tmp_path / filename)

    return _make


@pytest.fixture()
def primary_lines() -> List[str]:
    return list(PRIMARY_LINES)


@pytest.fixture(scope="session")
def parsed_bill(coserv_pdf):
    # Imported here so collection fails loudly if the package import path is wrong.
    from app.parsers.coserv_pdf_parser import parse_coserv_bill

    return parse_coserv_bill(coserv_pdf)
