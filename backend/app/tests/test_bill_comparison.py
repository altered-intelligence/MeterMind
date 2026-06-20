"""Tests for month-over-month bill comparison.

Pure-logic cases build detached ``Bill`` ORM objects directly (precise control of usage,
days, and totals); end-to-end cases go through the API to prove corrected values and the
``insufficient_history`` path.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Bill, ElectricService, GasService
from app.schemas import ChangeDirection
from app.services import bill_comparison_service as cmp


# --------------------------------------------------------------------------------------
# Builders for pure-logic tests
# --------------------------------------------------------------------------------------


def _bill(
    *,
    bill_id: int,
    bill_date: date,
    billing_days: int,
    total_due: str,
    current_charges: str,
    electric_total: str,
    kwh_used: str,
    kwh_per_day: str,
    electric_cost_per_day: str,
    eff_electric: str,
    gas_total: str = "0.00",
    ccf_used: str = "0",
    ccf_per_day: str = "0",
    gas_cost_per_day: str = "0.00",
    eff_gas: str = "0",
) -> Bill:
    b = Bill(
        id=bill_id,
        user_id=1,
        utility_account_id=1,
        provider_name="CoServ",
        bill_date=bill_date,
        billing_days=billing_days,
        total_amount_due=Decimal(total_due),
        current_charges_total=Decimal(current_charges),
        electric_service_total=Decimal(electric_total),
        gas_service_total=Decimal(gas_total),
    )
    b.electric_service = ElectricService(
        billing_days=billing_days,
        kwh_used=Decimal(kwh_used),
        average_kwh_per_day=Decimal(kwh_per_day),
        average_cost_per_day=Decimal(electric_cost_per_day),
        effective_rate_per_kwh=Decimal(eff_electric),
    )
    b.gas_service = GasService(
        billing_days=billing_days,
        ccf_used=Decimal(ccf_used),
        average_ccf_per_day=Decimal(ccf_per_day),
        average_cost_per_day=Decimal(gas_cost_per_day),
        effective_rate_per_ccf=Decimal(eff_gas),
    )
    return b


def _metric(comparison, key):
    return next(m for m in comparison.metrics if m.key == key)


# --------------------------------------------------------------------------------------
# Pure-logic comparisons
# --------------------------------------------------------------------------------------


def test_compare_two_bills_basic_changes():
    prev = _bill(
        bill_id=1, bill_date=date(2026, 5, 5), billing_days=32, total_due="274.00",
        current_charges="274.00", electric_total="200.00", kwh_used="1600",
        kwh_per_day="50.00", electric_cost_per_day="6.25", eff_electric="0.1250",
    )
    cur = _bill(
        bill_id=2, bill_date=date(2026, 6, 5), billing_days=32, total_due="317.00",
        current_charges="317.00", electric_total="239.14", kwh_used="1725",
        kwh_per_day="53.91", electric_cost_per_day="7.47", eff_electric="0.1386",
    )
    c = cmp._compare(cur, prev)
    assert c.status == "ok"
    assert c.bill_id == 2 and c.previous_bill_id == 1

    total = _metric(c, "total_amount_due")
    assert total.direction == ChangeDirection.INCREASED.value
    assert total.absolute_change == Decimal("43.00")
    assert total.percent_change is not None

    # Headline matches the spec's example shape.
    assert c.narrative.headline == (
        "Total amount due increased by $43.00, from $274.00 to $317.00."
    )
    # No anomaly language anywhere in the narrative.
    blob = " ".join([c.narrative.headline, *c.narrative.lines]).lower()
    assert not any(w in blob for w in ("high", "abnormal", "spike", "unusual"))


def test_per_day_metrics_marked_normalized():
    prev = _bill(
        bill_id=1, bill_date=date(2026, 5, 5), billing_days=30, total_due="100.00",
        current_charges="100.00", electric_total="100.00", kwh_used="900",
        kwh_per_day="30.00", electric_cost_per_day="3.33", eff_electric="0.1111",
    )
    cur = _bill(
        bill_id=2, bill_date=date(2026, 6, 5), billing_days=30, total_due="110.00",
        current_charges="110.00", electric_total="110.00", kwh_used="990",
        kwh_per_day="33.00", electric_cost_per_day="3.66", eff_electric="0.1111",
    )
    c = cmp._compare(cur, prev)
    for key in ("average_total_cost_per_day", "kwh_per_day", "electric_cost_per_day",
                "ccf_per_day", "gas_cost_per_day"):
        assert _metric(c, key).normalized is True
    for key in ("total_amount_due", "kwh_used", "electric_service_total", "effective_electric_rate"):
        assert _metric(c, key).normalized is False


def test_billing_days_normalization_separates_usage_from_period():
    """Same total kWh but a longer period: raw usage is unchanged while per-day DROPS, and
    the narrative flags the changed billing period."""
    prev = _bill(
        bill_id=1, bill_date=date(2026, 5, 5), billing_days=30, total_due="100.00",
        current_charges="100.00", electric_total="100.00", kwh_used="1500",
        kwh_per_day="50.00", electric_cost_per_day="3.33", eff_electric="0.0667",
    )
    cur = _bill(
        bill_id=2, bill_date=date(2026, 6, 5), billing_days=40, total_due="100.00",
        current_charges="100.00", electric_total="100.00", kwh_used="1500",
        kwh_per_day="37.50", electric_cost_per_day="2.50", eff_electric="0.0667",
    )
    c = cmp._compare(cur, prev)
    assert c.billing_days_changed is True

    kwh = _metric(c, "kwh_used")
    assert kwh.direction == ChangeDirection.UNCHANGED.value
    assert kwh.absolute_change == Decimal("0")

    per_day = _metric(c, "kwh_per_day")
    assert per_day.direction == ChangeDirection.DECREASED.value
    assert per_day.percent_change == Decimal("-25.0")  # 50 -> 37.5

    assert any("billing period increased from 30 days to 40 days" in l for l in c.narrative.lines)


def test_zero_previous_value_yields_no_percent_but_valid_direction():
    prev = _bill(
        bill_id=1, bill_date=date(2026, 5, 5), billing_days=30, total_due="100.00",
        current_charges="100.00", electric_total="100.00", kwh_used="900",
        kwh_per_day="30.00", electric_cost_per_day="3.33", eff_electric="0.1111",
        gas_total="0.00", ccf_used="0", ccf_per_day="0", gas_cost_per_day="0.00",
    )
    cur = _bill(
        bill_id=2, bill_date=date(2026, 6, 5), billing_days=30, total_due="120.00",
        current_charges="120.00", electric_total="100.00", kwh_used="900",
        kwh_per_day="30.00", electric_cost_per_day="3.33", eff_electric="0.1111",
        gas_total="20.00", ccf_used="10", ccf_per_day="0.33", gas_cost_per_day="0.66",
    )
    c = cmp._compare(cur, prev)
    gas = _metric(c, "gas_service_total")
    assert gas.previous_value == Decimal("0.00")
    assert gas.direction == ChangeDirection.INCREASED.value
    assert gas.percent_change is None  # division by zero previous -> undefined


# --------------------------------------------------------------------------------------
# End-to-end (API) — insufficient history + corrected values
# --------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_db():
    from app.database import SessionLocal, init_db
    from app.models import Bill as BillModel, UtilityAccount

    init_db()
    db = SessionLocal()
    try:
        for bill in db.query(BillModel).all():
            db.delete(bill)
        for acct in db.query(UtilityAccount).all():
            db.delete(acct)
        db.commit()
    finally:
        db.close()
    yield


def _variant_lines(primary, *, statement, due, after_due, kwh=None, total=None):
    """Copy PRIMARY_LINES, replacing whole lines so a second distinct bill can be built."""
    swaps = {
        "Statement Date: 06/05/2026": f"Statement Date: {statement}",
        "Due Date  06/22/2026": f"Due Date  {due}",
        "Amount Due After 06/22/2026  $328.25": f"Amount Due After {due}  {after_due}",
    }
    if kwh is not None:
        swaps["kWh Used: 1725"] = f"kWh Used: {kwh}"
    out = []
    for ln in primary:
        out.append(swaps.get(ln, ln))
    return out


def test_one_bill_returns_insufficient_history(coserv_pdf):
    with TestClient(app) as client:
        with open(coserv_pdf, "rb") as fh:
            client.post("/api/bills/upload", files={"file": ("a.pdf", fh, "application/pdf")})
        body = client.get("/api/analytics/latest-comparison").json()
        assert body["status"] == "insufficient_history"
        # The single bill's own comparison is also insufficient.
        bid = client.get("/api/bills").json()[0]["id"]
        assert client.get(f"/api/bills/{bid}/comparison").json()["status"] == "insufficient_history"


def test_comparison_uses_corrected_values(coserv_pdf, make_pdf, primary_lines):
    second = make_pdf(
        _variant_lines(primary_lines, statement="07/06/2026", due="07/22/2026",
                       after_due="$328.25", kwh="1800"),
        name="second.pdf",
    )
    with TestClient(app) as client:
        with open(coserv_pdf, "rb") as fh:
            client.post("/api/bills/upload", files={"file": ("a.pdf", fh, "application/pdf")})
        with open(second, "rb") as fh:
            r = client.post("/api/bills/upload", files={"file": ("b.pdf", fh, "application/pdf")})
        cur_id = r.json()["id"]

        # Correct the latest bill's kWh, then the comparison must use the corrected value.
        client.post(
            f"/api/bills/{cur_id}/corrections",
            json={"field_path": "electric.kwh_used", "corrected_value": "2000", "reason": "fix"},
        )
        comp = client.get(f"/api/bills/{cur_id}/comparison").json()
        assert comp["status"] == "ok"
        kwh = next(m for m in comp["metrics"] if m["key"] == "kwh_used")
        assert kwh["current_value"] == "2000"
        # Per-day metric is present and flagged normalized.
        per_day = next(m for m in comp["metrics"] if m["key"] == "kwh_per_day")
        assert per_day["normalized"] is True

        # No anomaly language in the API output.
        import json as _json
        assert not any(
            w in _json.dumps(comp).lower() for w in ("abnormal", "spike", "unusual")
        )
