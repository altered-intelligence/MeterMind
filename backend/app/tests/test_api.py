"""End-to-end API tests over the normalized schema (FastAPI TestClient).

Uses the synthetic ``coserv_pdf`` fixture so no real bill is required. Storage and the
SQLite DB are redirected to a temp directory by conftest. Each test starts from a clean
bills/accounts table.
"""

from __future__ import annotations

import json
import logging
import os

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(autouse=True)
def _clean_db():
    """Reset bills + utility_accounts before each API test for isolation."""
    from app.database import SessionLocal, init_db
    from app.models import Bill, UtilityAccount

    init_db()
    db = SessionLocal()
    try:
        for bill in db.query(Bill).all():
            db.delete(bill)
        for acct in db.query(UtilityAccount).all():
            db.delete(acct)
        db.commit()
    finally:
        db.close()
    yield


def _upload(client: TestClient, path: str, name: str = "bill.pdf") -> dict:
    with open(path, "rb") as fh:
        resp = client.post("/api/bills/upload", files={"file": (name, fh, "application/pdf")})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_upload_parses_and_redacts_pii(coserv_pdf):
    with TestClient(app) as client:
        bill = _upload(client, coserv_pdf)
        assert bill["parser_confidence"] == "high"
        assert bill["electric_service"]["kwh_used"] == 1725
        assert bill["gas_service"]["ccf_used"] == 36

        s = bill["account_summary"]
        # Only last4 + redacted forms — never the full number/address.
        assert s["account_number_last4"] == "7890"
        assert s["account_number_redacted"] == "••••7890"
        assert s["service_address_redacted"] == "Aubrey, TX 76227"

        # The full account number / street must not appear anywhere in the response.
        blob = str(bill)
        assert "1234567890" not in blob
        assert "123 Sample Road" not in blob

        # utility_account linked; granular charges + derived present.
        assert bill["utility_account"]["account_number_last4"] == "7890"
        assert len(bill["electric_service"]["line_items"]) == 7
        assert bill["derived"]["effective_electric_rate"] is not None
        # a credit line item is flagged
        # (synthetic fixture has no negative electric line, but PCRF on real bills is a credit)


def test_list_get_json_delete(coserv_pdf):
    with TestClient(app) as client:
        bid = _upload(client, coserv_pdf)["id"]

        listing = client.get("/api/bills").json()
        assert any(b["id"] == bid for b in listing)

        got = client.get(f"/api/bills/{bid}").json()
        assert got["id"] == bid

        exported = client.get(f"/api/bills/{bid}/json").json()
        assert exported["account_summary"]["account_number_last4"] == "7890"
        assert "1234567890" not in str(exported)

        assert client.delete(f"/api/bills/{bid}").status_code == 204
        assert client.get(f"/api/bills/{bid}").status_code == 404
        assert client.get("/api/bills").json() == []


def test_debug_view_no_pii_leak(coserv_pdf):
    with TestClient(app) as client:
        bid = _upload(client, coserv_pdf)["id"]
        blocks = client.get(f"/api/bills/{bid}/debug").json()["redacted_text_blocks"]
        assert "1234567890" not in blocks
        assert "123 Sample Road" not in blocks


def test_same_account_dedupes_to_one_utility_account(coserv_pdf, make_pdf, primary_lines):
    # Same account number, two DIFFERENT statement dates (not duplicates).
    may = [ln.replace("06/05/2026", "05/07/2026") for ln in primary_lines]
    with TestClient(app) as client:
        a = _upload(client, make_pdf(may), "may.pdf")
        b = _upload(client, coserv_pdf, "jun.pdf")
        assert a["utility_account"]["id"] == b["utility_account"]["id"]  # deduped account
        assert len(client.get("/api/bills").json()) == 2  # two distinct bills


def test_duplicate_bill_rejected(coserv_pdf):
    with TestClient(app) as client:
        assert _upload(client, coserv_pdf, "first.pdf")  # first upload succeeds
        with open(coserv_pdf, "rb") as fh:
            resp = client.post("/api/bills/upload", files={"file": ("again.pdf", fh, "application/pdf")})
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "duplicate_bill"
        assert len(client.get("/api/bills").json()) == 1  # not stored twice


def test_analytics_summary(coserv_pdf, make_pdf, primary_lines):
    # Second bill: later date, higher kWh -> a real month-over-month delta.
    later = [
        ln.replace("06/05/2026", "07/05/2026").replace("kWh Used: 1725", "kWh Used: 1800")
        for ln in primary_lines
    ]
    with TestClient(app) as client:
        _upload(client, coserv_pdf, "june.pdf")
        _upload(client, make_pdf(later), "july.pdf")

        summary = client.get("/api/analytics/summary").json()
        assert summary["total_bills"] == 2
        assert summary["latest_bill_amount"] == "317.00"
        assert summary["latest_electric_usage_kwh"] == 1800  # the later bill
        assert summary["latest_gas_usage_ccf"] == 36
        assert summary["month_over_month"]["electric_kwh"]["absolute"] == "75"  # 1800-1725
        assert summary["month_over_month"]["total_bill"]["absolute"] == "0.00"
        assert summary["average_electric_effective_rate"] is not None
        assert summary["average_gas_effective_rate"] is not None
        assert summary["highest_electric_usage_bill"]["kwh_used"] == 1800
        assert summary["highest_total_bill"]["total_amount_due"] == "317.00"


def test_analytics_electric_and_gas(coserv_pdf, make_pdf, primary_lines):
    later = [
        ln.replace("06/05/2026", "07/05/2026").replace("kWh Used: 1725", "kWh Used: 1800")
        for ln in primary_lines
    ]
    with TestClient(app) as client:
        _upload(client, coserv_pdf, "june.pdf")
        _upload(client, make_pdf(later), "july.pdf")

        el = client.get("/api/analytics/electric").json()
        assert el["service"] == "electric"
        assert el["bill_count"] == 2
        assert len(el["series"]) == 2
        assert el["latest_usage_kwh"] == 1800
        assert el["total_kwh"] == 3525  # 1725 + 1800
        assert el["month_over_month_kwh"]["absolute"] == "75"

        gas = client.get("/api/analytics/gas").json()
        assert gas["service"] == "gas"
        assert gas["bill_count"] == 2
        assert gas["total_ccf"] == 72  # 36 + 36
        assert gas["latest_usage_ccf"] == 36


def test_analytics_summary_empty():
    with TestClient(app) as client:
        summary = client.get("/api/analytics/summary").json()
        assert summary["total_bills"] == 0
        assert summary["latest"] is None


def test_ingest_failure_leaves_no_orphaned_pdf():
    from app.database import SessionLocal
    from app.services import bill_service
    from app.storage import get_storage

    def boom(_path):
        raise RuntimeError("simulated parse failure")

    import app.services.bill_service as bs

    with TestClient(app):  # ensure tables exist
        pass

    monkey = pytest.MonkeyPatch()
    monkey.setattr(bs, "parse_coserv_bill", boom)
    storage = get_storage()
    before = set(os.listdir(storage.base_dir))
    db = SessionLocal()
    try:
        with pytest.raises(RuntimeError):
            bill_service.ingest_bill(db, file_bytes=b"%PDF-1.4 fake", filename="x.pdf")
    finally:
        db.close()
        monkey.undo()
    assert set(os.listdir(storage.base_dir)) == before  # no orphan


def test_rejects_non_pdf_with_error_envelope():
    with TestClient(app) as client:
        resp = client.post(
            "/api/bills/upload", files={"file": ("notes.txt", b"hi", "text/plain")}
        )
        assert resp.status_code == 415
        assert resp.json()["error"]["code"] == "invalid_pdf"


def test_health_and_version():
    with TestClient(app) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        v = client.get("/api/version").json()
        assert v["parser_version"] == "coserv-v0.1.0"
        assert v["app_version"]


def test_parser_result_wrapper(coserv_pdf):
    from app.parsers.coserv_pdf_parser import parse_coserv_bill_result

    result = parse_coserv_bill_result(coserv_pdf)
    assert set(result.keys()) == {"success", "bill", "warnings", "parser_version", "parser_confidence"}
    assert result["success"] is True
    assert result["parser_version"] == "coserv-v0.1.0"
    assert result["parser_confidence"] == "high"
    assert result["bill"]["account_summary"]["total_amount_due"] == "317.00"  # Decimal as string


def test_money_serialized_as_strings(coserv_pdf):
    with TestClient(app) as client:
        bill = _upload(client, coserv_pdf)
        s = bill["account_summary"]
        for field in ("total_amount_due", "electric_service_total", "gas_service_total"):
            assert isinstance(s[field], str)  # Decimals serialized as strings, never floats
        assert isinstance(bill["electric_service"]["total_current_charges"], str)


def test_no_sensitive_data_in_logs(coserv_pdf, caplog):
    from app.database import SessionLocal
    from app.services import bill_service

    with caplog.at_level(logging.INFO):
        db = SessionLocal()
        try:
            with open(coserv_pdf, "rb") as fh:
                bill_service.ingest_bill(db, file_bytes=fh.read(), filename="b.pdf")
        finally:
            db.close()

    text = caplog.text
    # Sensitive values must NOT appear in logs.
    assert "1234567890" not in text  # full account number
    assert "123 Sample Road" not in text  # service address
    assert "000123456" not in text and "000654321" not in text  # meter numbers
    # Safe, useful fields ARE logged.
    assert "parser_version=coserv-v0.1.0" in text
    assert "bill_date=2026-06-05" in text
    assert "account=••••7890" in text
    assert "confidence=high" in text


def test_config_canonical_and_backcompat_env(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("ENABLE_LOCAL_LLM", "true")
    monkeypatch.setenv("ENABLE_WEATHER", "true")
    monkeypatch.setenv("DEBUG_PARSER", "true")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///canonical.db")
    monkeypatch.setenv("PDF_STORAGE_PATH", "/tmp/hui_cfg_bills")
    monkeypatch.setenv("APP_SECRET", "s3cret")
    s = Settings()
    assert s.enable_local_llm is True and s.ollama_enabled is True  # property mirrors flag
    assert s.enable_weather is True and s.debug_parser is True
    assert s.database_url == "sqlite:///canonical.db"
    assert str(s.storage_dir) == "/tmp/hui_cfg_bills"
    assert s.app_secret == "s3cret"

    # Back-compat: the old OLLAMA_ENABLED name still enables the local LLM.
    monkeypatch.delenv("ENABLE_LOCAL_LLM", raising=False)
    monkeypatch.setenv("OLLAMA_ENABLED", "true")
    assert Settings().ollama_enabled is True


def _audit_check(result: dict, code: str) -> dict:
    return next(c for c in result["checks"] if c["check_code"] == code)


def test_audit_clean_bill_passes(coserv_pdf):
    with TestClient(app) as client:
        bid = _upload(client, coserv_pdf)["id"]
        result = client.get(f"/api/bills/{bid}/audit").json()

        assert result["overall_status"] == "pass"
        assert result["audit_version"] == "audit-v0.1.0"
        assert len(result["checks"]) == 14
        # all four groups represented
        assert {c["group"] for c in result["checks"]} == {
            "account_summary", "electric", "gas", "snapshot"
        }
        # every check has the required structured fields
        for c in result["checks"]:
            assert {"check_code", "label", "group", "status", "severity"} <= set(c.keys())
            assert c["status"] in ("pass", "warning", "fail", "skipped")
        # spot-check a couple of identities
        assert _audit_check(result, "electric_plus_gas_equals_current_charges")["status"] == "pass"
        assert _audit_check(result, "electric_meter_delta_matches_kwh")["status"] == "pass"
        assert not any(c["status"] == "fail" for c in result["checks"])


def test_audit_meter_mismatch_fails(coserv_pdf, make_pdf, primary_lines):
    # Tamper the current read so (current - last) * mult no longer equals kWh used.
    tampered = [ln.replace("Current Reading: 46955", "Current Reading: 50000") for ln in primary_lines]
    with TestClient(app) as client:
        bid = _upload(client, make_pdf(tampered))["id"]
        result = client.get(f"/api/bills/{bid}/audit").json()
        check = _audit_check(result, "electric_meter_delta_matches_kwh")
        assert check["status"] == "fail"
        assert check["severity"] == "high"
        assert check["expected_value"] == "4770"  # (50000-45230)*1
        assert check["actual_value"] == "1725"
        assert result["overall_status"] == "fail"


def test_audit_late_below_current_warns(coserv_pdf, make_pdf, primary_lines):
    tampered = [ln.replace("Amount Due After 06/22/2026  $328.25", "Amount Due After 06/22/2026  $300.00") for ln in primary_lines]
    with TestClient(app) as client:
        bid = _upload(client, make_pdf(tampered))["id"]
        result = client.get(f"/api/bills/{bid}/audit").json()
        check = _audit_check(result, "late_amount_greater_than_or_equal_current_amount")
        assert check["status"] == "warning"
        assert result["overall_status"] == "warning"  # warning, not fail (no hard identity broke)


def test_audit_skipped_when_data_missing():
    from app.models import Bill
    from app.services.bill_audit_service import run_audit

    bill = Bill()
    bill.line_items = []
    bill.snapshots = []
    bill.electric_service = None
    bill.gas_service = None
    result = run_audit(bill)

    by_code = {c.check_code: c for c in result.checks}
    assert by_code["electric_meter_delta_matches_kwh"].status == "skipped"
    assert by_code["gas_meter_delta_matches_ccf"].status == "skipped"
    assert by_code["electric_average_cost_per_day_matches_total_if_available"].status == "skipped"
    # nothing failed or warned -> overall pass even though most checks were skipped
    assert result.overall_status == "pass"


def test_audit_persisted(coserv_pdf):
    with TestClient(app) as client:
        bid = _upload(client, coserv_pdf)["id"]
        first = client.get(f"/api/bills/{bid}/audit").json()
        second = client.get(f"/api/bills/{bid}/audit").json()
        # Same stored result (created_at identical) -> persisted, not recomputed each call.
        assert first["created_at"] == second["created_at"]


def test_health():
    with TestClient(app) as client:
        assert client.get("/api/health").json()["status"] == "ok"


def test_portal_status_is_disabled_placeholder():
    with TestClient(app) as client:
        s = client.get("/api/portal/status").json()
        assert s["enabled"] is False
        assert s["browser_automation_enabled"] is False
        assert s["available"] is False
        assert "SmartHub" in s["message"]
        # the documented failure modes + constraints are surfaced
        assert "mfa_required" in s["supported_failure_modes"]
        assert "captcha_encountered" in s["supported_failure_modes"]
        assert any("MFA" in c or "CAPTCHA" in c for c in s["constraints"])


def test_portal_sync_not_implemented():
    with TestClient(app) as client:
        resp = client.post("/api/portal/sync")
        assert resp.status_code == 501  # no automation in this build


def test_insights_single_bill_matches_example_wording(coserv_pdf):
    with TestClient(app) as client:
        _upload(client, coserv_pdf)
        ins = client.get("/api/analytics/insights").json()

        s = ins["summary"]
        assert "Your latest bill is $317.00, with $239.14 for electric service and $77.86 for gas service." in s
        assert "Electric usage was 1,725 kWh over 32 days, averaging 53.91 kWh/day." in s
        assert "Gas usage was 36 CCF over 32 days, averaging 1.13 CCF/day." in s
        assert "Effective electric rate was approximately $0.139/kWh." in s
        assert "Effective gas rate was approximately $2.16/CCF." in s
        assert "More history is needed before anomaly detection can be considered reliable." in s

        # Short history -> warning, no anomalies claimed.
        assert any("More history is needed" in w for w in ins["warnings"])
        assert ins["anomalies"] == []

        m = ins["metrics"]
        assert m["latest_total_bill"] == "317.00"
        assert m["latest_kwh"] == 1725
        assert m["latest_ccf"] == 36
        assert m["previous_total_bill"] is None  # only one bill


def test_insights_detects_high_electric_usage(coserv_pdf, make_pdf, primary_lines):
    def variant(date_str, kwh):
        return [
            ln.replace("06/05/2026", date_str).replace("kWh Used: 1725", f"kWh Used: {kwh}")
            for ln in primary_lines
        ]

    with TestClient(app) as client:
        _upload(client, make_pdf(variant("03/06/2026", 1000)), "mar.pdf")
        _upload(client, make_pdf(variant("04/07/2026", 1000)), "apr.pdf")
        _upload(client, make_pdf(variant("06/05/2026", 1500)), "jun.pdf")  # latest, +50%/day

        ins = client.get("/api/analytics/insights").json()
        # 3 bills -> reliable, so no insufficient-history warning
        assert not any("More history is needed" in w for w in ins["warnings"])
        types = {a["type"] for a in ins["anomalies"]}
        assert "high_electric_usage" in types
        assert ins["metrics"]["latest_kwh"] == 1500
        assert ins["metrics"]["kwh_change_percent"] is not None
        # a recommendation is offered for the flagged anomaly
        assert any("Electric usage per day" in r for r in ins["recommendations"])


def test_bill_summary_deterministic_when_llm_disabled(coserv_pdf):
    with TestClient(app) as client:
        bid = _upload(client, coserv_pdf)["id"]
        out = client.get(f"/api/bills/{bid}/summary").json()

        assert "$317.00" in out["deterministic_summary"]
        assert "1,725 kWh" in out["deterministic_summary"]
        assert out["llm_enabled"] is False
        assert out["llm_summary"] is None
        assert out["llm_status"] == "disabled"

        # Redacted input matches the spec shape exactly...
        ri = out["redacted_input"]
        assert set(ri.keys()) == {"bill_date", "total_amount_due", "electric", "gas", "warnings"}
        assert ri["total_amount_due"] == 317.0
        assert ri["electric"] == {
            "total": 239.14, "kwh": 1725, "days": 32,
            "average_kwh_per_day": 53.91, "effective_rate": 0.1386,
        }
        assert ri["gas"] == {
            "total": 77.86, "ccf": 36, "days": 32,
            "average_ccf_per_day": 1.13, "effective_rate": 2.1628,
        }
        # ...and carries NO PII (this is the only thing the model would ever receive).
        blob = json.dumps(out)
        assert "1234567890" not in blob  # account number
        assert "123 Sample Road" not in blob  # service address
        assert "meter" not in json.dumps(ri).lower()


def test_summary_payload_includes_history_with_multiple_bills(coserv_pdf, make_pdf, primary_lines):
    def variant(date_str, kwh):
        return [
            ln.replace("06/05/2026", date_str).replace("kWh Used: 1725", f"kWh Used: {kwh}")
            for ln in primary_lines
        ]

    with TestClient(app) as client:
        _upload(client, make_pdf(variant("04/07/2026", 1400)), "apr.pdf")
        _upload(client, make_pdf(variant("05/07/2026", 1500)), "may.pdf")
        bid = _upload(client, coserv_pdf, "jun.pdf")["id"]  # June (1725 kWh) — the latest

        ri = client.get(f"/api/bills/{bid}/summary").json()["redacted_input"]
        # With history, the model can compare to the previous bill...
        assert "history" in ri
        h = ri["history"]
        assert h["bill_count"] == 3
        assert h["previous_bill"]["bill_date"] == "2026-05-07"  # the bill before June
        assert h["changes_vs_previous"]["kwh_change_percent"] is not None  # 1725 vs 1500
        assert h["rolling_average_total_bill"] is not None
        # ...and STILL no PII anywhere.
        assert "1234567890" not in json.dumps(ri)
        assert "123 Sample Road" not in json.dumps(ri)


def test_assistant_status_and_chat_disabled(coserv_pdf):
    with TestClient(app) as client:
        _upload(client, coserv_pdf)
        status = client.get("/api/assistant/status").json()
        assert status["enabled"] is False  # Ollama off by default
        assert "coserv_bills" in status["sources"]  # the extensible registry is wired

        # Chat is 409 when disabled (frontend shows an "enable Ollama" note).
        r = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 409


def test_assistant_models_endpoint_shape():
    with TestClient(app) as client:
        m = client.get("/api/assistant/models").json()
        assert set(m.keys()) == {"enabled", "active", "available"}
        assert isinstance(m["available"], list)
        assert isinstance(m["active"], str) and m["active"]  # some active model is reported


def test_active_model_get_set(tmp_path, monkeypatch):
    from app.services import ollama_runtime

    monkeypatch.setattr(ollama_runtime, "_model_file", lambda: tmp_path / ".ai_model")
    assert ollama_runtime.get_active_model() == "llama3.1"  # falls back to env default
    ollama_runtime.set_active_model("mistral")
    assert ollama_runtime.get_active_model() == "mistral"  # UI choice persists + overrides


def test_resolve_model_treats_latest_as_same():
    from app.services.ollama_runtime import resolve_model

    avail = ["llama3.2:latest", "qwen3.6:27b"]
    assert resolve_model("llama3.2", avail) == "llama3.2:latest"  # bare name -> :latest tag
    assert resolve_model("llama3.2:latest", avail) == "llama3.2:latest"
    assert resolve_model("qwen3.6:27b", avail) == "qwen3.6:27b"
    assert resolve_model("not-installed", avail) is None


def test_assistant_context_covers_all_bills_no_pii(coserv_pdf, make_pdf, primary_lines):
    from app.database import SessionLocal
    from app.services import assistant_service, bill_service

    def variant(date_str, kwh):
        return [
            ln.replace("06/05/2026", date_str).replace("kWh Used: 1725", f"kWh Used: {kwh}")
            for ln in primary_lines
        ]

    with TestClient(app) as client:
        _upload(client, make_pdf(variant("04/07/2026", 1400)), "apr.pdf")
        _upload(client, make_pdf(variant("05/07/2026", 1500)), "may.pdf")
        _upload(client, coserv_pdf, "jun.pdf")

    db = SessionLocal()
    try:
        user = bill_service.get_or_create_default_user(db)
        ctx = assistant_service.build_context(db, user.id)
    finally:
        db.close()

    cb = ctx["sources"]["coserv_bills"]
    assert cb["bill_count"] == 3  # the assistant sees ALL bills, not just one
    assert len(cb["bills"]) == 3
    assert cb["aggregates"]["highest_total_bill"] is not None
    # No PII anywhere in what the model receives.
    blob = json.dumps(ctx)
    assert "1234567890" not in blob and "123 Sample Road" not in blob
    assert "meter" not in blob.lower()


def test_summary_stream_disabled_returns_409(coserv_pdf):
    with TestClient(app) as client:
        bid = _upload(client, coserv_pdf)["id"]
        # /summary stays fast and never blocks on the model.
        assert client.get(f"/api/bills/{bid}/summary").json()["llm_status"] == "disabled"
        # Streaming endpoint is 409 when Ollama is disabled (client uses deterministic).
        assert client.get(f"/api/bills/{bid}/summary/stream").status_code == 409
        assert client.get("/api/bills/999999/summary/stream").status_code == 404


def test_llm_summary_falls_back_when_ollama_unreachable():
    from app.services import llm_summary_service as llm

    payload = {
        "bill_date": "2026-06-05",
        "total_amount_due": 317.0,
        "electric": None,
        "gas": None,
        "warnings": [],
    }
    text, status = llm.generate_llm_summary(
        payload, model="llama3.1", host="http://127.0.0.1:9", timeout=2.0
    )
    assert text is None
    assert status in ("unavailable", "error")


def test_insights_empty_history():
    with TestClient(app) as client:
        ins = client.get("/api/analytics/insights").json()
        assert "No bills" in ins["summary"]
        assert ins["metrics"] == {}
        assert ins["anomalies"] == []
        assert ins["warnings"]


def test_portal_connector_disabled_blocks_actions():
    import asyncio

    from app.connectors.coserv_portal_connector import (
        DisabledPortalConnector,
        PortalDisabledError,
    )

    conn = DisabledPortalConnector()
    assert conn.status().enabled is False
    with pytest.raises(PortalDisabledError):
        asyncio.run(conn.sync_bills(None))
    with pytest.raises(PortalDisabledError):
        asyncio.run(conn.begin_interactive_login())
