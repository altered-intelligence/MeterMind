"""Unit + API tests for the deterministic rate & fee breakdown service."""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Bill, ChargeLineItem, ElectricService, GasService
from app.services import rate_breakdown_service as rb


@pytest.fixture(autouse=True)
def _clean_db():
    """Reset bills + utility_accounts before each API test for isolation."""
    from app.database import SessionLocal, init_db
    from app.models import Bill as BillRow, UtilityAccount

    init_db()
    db = SessionLocal()
    try:
        for bill in db.query(BillRow).all():
            db.delete(bill)
        for acct in db.query(UtilityAccount).all():
            db.delete(acct)
        db.commit()
    finally:
        db.close()
    yield


# --------------------------------------------------------------------------------------
# Synthetic ORM builders (no DB needed)
# --------------------------------------------------------------------------------------


def _li(service_type: str, label: str, amount, *, quantity=None, unit=None, rate=None):
    return ChargeLineItem(
        service_type=service_type,
        label=label,
        amount=Decimal(str(amount)) if amount is not None else None,
        quantity=Decimal(str(quantity)) if quantity is not None else None,
        unit=unit,
        rate=Decimal(str(rate)) if rate is not None else None,
        is_credit=(amount is not None and Decimal(str(amount)) < 0),
    )


def _electric_bill():
    """A representative electric section that reconciles to $211.19 over 1,725 kWh."""
    bill = Bill()
    bill.id = 1
    bill.gas_service = None
    bill.electric_service = ElectricService(
        kwh_used=Decimal("1725"), total_current_charges=Decimal("211.19")
    )
    bill.line_items = [
        _li("electric", "Customer Charge", "25.00"),
        _li("electric", "Energy Charge 1,725 kWh", "150.00", quantity=1725, unit="kWh"),
        _li("electric", "PCRF 1,725 kWh @ -$0.006", "-10.35", quantity=1725, rate="-0.006"),
        _li("electric", "SCRF", "5.00"),
        _li("electric", "Municipal Franchise Fee", "8.00"),
        _li("electric", "Sales Tax", "30.04"),
        _li("electric", "Operation Roundup", "3.50"),
        _li("electric", "Total Current Charges", "211.19"),  # must be excluded from sum
    ]
    return bill


def _gas_bill():
    """A gas section that reconciles to $127.81 over 50 CCF."""
    bill = Bill()
    bill.id = 2
    bill.electric_service = None
    bill.gas_service = GasService(
        ccf_used=Decimal("50"), total_current_charges=Decimal("127.81")
    )
    bill.line_items = [
        _li("gas", "Customer Charge", "19.75"),
        _li("gas", "Interim Rate Adjustment", "1.25"),
        _li("gas", "Usage Charge 50 CCF", "40.00", quantity=50, unit="CCF"),
        _li("gas", "Purchased Gas Factor 50 CCF", "55.00", quantity=50),
        _li("gas", "Municipal Franchise Fee", "4.00"),
        _li("gas", "Gross Receipts Tax", "3.81"),
        _li("gas", "Sales Tax", "4.00"),
        _li("gas", "Total Current Charges", "127.81"),
    ]
    return bill


def _cats(service):
    return {c.key: c for c in service.categories}


# --------------------------------------------------------------------------------------
# Categorization
# --------------------------------------------------------------------------------------


def test_electric_categorization_maps_each_label():
    svc = rb.build_breakdown(_electric_bill()).electric
    cats = _cats(svc)
    assert cats["fixed_charges"].amount == "25.00"
    assert cats["variable_energy_charges"].amount == "150.00"
    assert cats["credits_and_adjustments"].amount == "-5.35"  # -10.35 PCRF + 5.00 SCRF
    assert cats["local_fees"].amount == "8.00"
    assert cats["taxes"].amount == "30.04"
    assert cats["optional_or_rounding"].amount == "3.50"
    # the printed total is surfaced but never summed into the categories
    assert cats["total"].amount == "211.19"
    assert svc.total_categorized == "211.19"


def test_gas_categorization_maps_each_label():
    svc = rb.build_breakdown(_gas_bill()).gas
    cats = _cats(svc)
    assert cats["fixed_charges"].amount == "19.75"
    assert cats["adjustments"].amount == "1.25"
    assert cats["variable_usage_charges"].amount == "40.00"
    assert cats["purchased_gas_charges"].amount == "55.00"
    assert cats["local_fees"].amount == "4.00"
    # Gross Receipts Tax + Sales Tax both fold into taxes
    assert cats["taxes"].amount == "7.81"


def test_total_line_excluded_from_category_sum():
    svc = rb.build_breakdown(_electric_bill()).electric
    # categories (excluding `total`) reconcile to the service total within $0.01
    assert svc.reconciles is True
    assert Decimal(svc.total_categorized) == Decimal("211.19")


# --------------------------------------------------------------------------------------
# Effective-rate calculations
# --------------------------------------------------------------------------------------


def test_electric_effective_rates():
    m = rb.build_breakdown(_electric_bill()).electric.metrics
    assert m["true_effective_rate_per_kwh"] == "0.1224"   # 211.19 / 1725
    assert m["energy_only_rate_per_kwh"] == "0.0870"       # 150.00 / 1725
    assert m["kwh_used"] == "1725"
    # fixed 25 / 211.19 ~= 0.1184
    assert m["fixed_cost_percentage"] == "0.1184"
    assert m["variable_cost_percentage"] == "0.7103"       # 150 / 211.19


def test_gas_effective_rates():
    m = rb.build_breakdown(_gas_bill()).gas.metrics
    assert m["true_effective_rate_per_ccf"] == "2.5562"     # 127.81 / 50
    assert m["purchased_gas_rate_per_ccf"] == "1.1000"      # 55.00 / 50
    assert m["commodity_cost_percentage"] == "0.4303"       # 55 / 127.81


def test_true_rate_exceeds_energy_only_rate():
    m = rb.build_breakdown(_electric_bill()).electric.metrics
    assert Decimal(m["true_effective_rate_per_kwh"]) > Decimal(m["energy_only_rate_per_kwh"])


# --------------------------------------------------------------------------------------
# Edge cases
# --------------------------------------------------------------------------------------


def test_zero_usage_yields_no_rate():
    bill = _electric_bill()
    bill.electric_service.kwh_used = Decimal("0")
    m = rb.build_breakdown(bill).electric.metrics
    assert m["true_effective_rate_per_kwh"] is None
    assert m["energy_only_rate_per_kwh"] is None
    # percentages (denominator is charges, not usage) are still computed
    assert m["fixed_cost_percentage"] == "0.1184"


def test_missing_line_items_zero_categories():
    bill = Bill()
    bill.id = 3
    bill.gas_service = None
    bill.electric_service = ElectricService(
        kwh_used=Decimal("1000"), total_current_charges=Decimal("0")
    )
    bill.line_items = []
    svc = rb.build_breakdown(bill).electric
    cats = _cats(svc)
    assert cats["fixed_charges"].amount == "0.00"
    assert cats["variable_energy_charges"].amount == "0.00"
    assert svc.total_categorized == "0.00"


def test_credit_amount_reduces_category_total():
    svc = rb.build_breakdown(_electric_bill()).electric
    cat = _cats(svc)["credits_and_adjustments"]
    # PCRF credit (-10.35) nets against SCRF (5.00) -> negative category
    assert Decimal(cat.amount) < 0
    assert cat.amount == "-5.35"


def test_unknown_label_is_uncategorized_and_warns():
    bill = _electric_bill()
    bill.line_items.insert(0, _li("electric", "Mystery Rider", "12.00"))
    bill.electric_service.total_current_charges = Decimal("223.19")  # 211.19 + 12.00
    svc = rb.build_breakdown(bill).electric
    cats = _cats(svc)
    assert "uncategorized" in cats
    assert cats["uncategorized"].amount == "12.00"
    assert "Mystery Rider" in svc.uncategorized_labels
    assert any("Mystery Rider" in w for w in svc.warnings)
    # still reconciles because uncategorized items are summed
    assert svc.reconciles is True


def test_reconciliation_warning_when_total_mismatches():
    bill = _electric_bill()
    bill.electric_service.total_current_charges = Decimal("999.99")  # deliberately wrong
    svc = rb.build_breakdown(bill).electric
    assert svc.reconciles is False
    assert any("Total Current Charges" in w for w in svc.warnings)


# --------------------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------------------


def test_deterministic_output():
    a = rb.build_breakdown(_electric_bill()).electric.model_dump()
    b = rb.build_breakdown(_electric_bill()).electric.model_dump()
    assert a == b


# --------------------------------------------------------------------------------------
# API wiring
# --------------------------------------------------------------------------------------


def test_rate_breakdown_endpoint(coserv_pdf):
    from app.tests.test_api import _upload  # reuse the upload helper

    with TestClient(app) as client:
        bid = _upload(client, coserv_pdf)["id"]
        res = client.get(f"/api/bills/{bid}/rate-breakdown")
        assert res.status_code == 200
        body = res.json()
        assert body["breakdown_version"] == rb.RATE_BREAKDOWN_VERSION
        assert body["electric"] is not None
        assert body["gas"] is not None
        # the real synthetic fixture should reconcile on both services
        assert body["electric"]["reconciles"] is True
        assert body["gas"]["reconciles"] is True


def test_rate_breakdown_trends_endpoint(coserv_pdf):
    from app.tests.test_api import _upload

    with TestClient(app) as client:
        _upload(client, coserv_pdf)
        res = client.get("/api/analytics/rate-breakdown-trends")
        assert res.status_code == 200
        points = res.json()
        assert len(points) >= 1
        assert {"bill_id", "bill_date", "electric_effective_rate_per_kwh"} <= set(points[0])


def test_rate_breakdown_404_for_missing_bill():
    with TestClient(app) as client:
        assert client.get("/api/bills/999999/rate-breakdown").status_code == 404
