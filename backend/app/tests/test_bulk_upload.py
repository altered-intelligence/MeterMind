"""Tests for multi-bill bulk upload + deterministic duplicate detection."""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import bill_service


@pytest.fixture(autouse=True)
def _clean_db():
    """Reset bills, accounts, and import batches before each test for isolation."""
    from app.database import SessionLocal, init_db
    from app.models import Bill, ImportBatch, UtilityAccount

    init_db()
    db = SessionLocal()
    try:
        for row in db.query(ImportBatch).all():
            db.delete(row)
        for bill in db.query(Bill).all():
            db.delete(bill)
        for acct in db.query(UtilityAccount).all():
            db.delete(acct)
        db.commit()
    finally:
        db.close()
    yield


def _bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _bulk(client: TestClient, files: list[tuple[str, bytes]]) -> dict:
    multipart = [("files", (name, data, "application/pdf")) for name, data in files]
    resp = client.post("/api/bills/bulk-upload", files=multipart)
    assert resp.status_code == 201, resp.text
    return resp.json()


# --------------------------------------------------------------------------------------
# Unit: deterministic duplicate detection
# --------------------------------------------------------------------------------------


def test_find_duplicate_unit():
    from app.database import SessionLocal
    from app.models import Bill, UtilityAccount, User

    db = SessionLocal()
    try:
        user = bill_service.get_or_create_default_user(db)
        acct = UtilityAccount(
            user_id=user.id, provider_name="CoServ",
            account_number_hash="acct-hash-xyz", account_number_last4="7890",
        )
        db.add(acct)
        db.flush()
        bill = Bill(
            user_id=user.id, utility_account_id=acct.id, provider_name="CoServ",
            bill_date=__import__("datetime").date(2026, 5, 7),
            total_amount_due=Decimal("317.00"),
            pdf_sha256_hash="pdf-hash-abc",
        )
        db.add(bill)
        db.commit()

        d = __import__("datetime").date(2026, 5, 7)

        # 1. Exact-file match (pdf hash) — account/date/total irrelevant.
        hit = bill_service._find_duplicate(
            db, user.id, pdf_hash="pdf-hash-abc", account=None, bill_date=None, total_amount_due=None
        )
        assert hit is not None and hit.id == bill.id

        # 2. Composite match: same account + bill_date + total.
        hit = bill_service._find_duplicate(
            db, user.id, pdf_hash="different", account=acct, bill_date=d,
            total_amount_due=Decimal("317.00"),
        )
        assert hit is not None and hit.id == bill.id

        # 3. Different total -> NOT a duplicate.
        assert bill_service._find_duplicate(
            db, user.id, pdf_hash="different", account=acct, bill_date=d,
            total_amount_due=Decimal("999.00"),
        ) is None

        # 4. No signals at all -> None (deterministic, never a false positive).
        assert bill_service._find_duplicate(
            db, user.id, pdf_hash=None, account=None, bill_date=None, total_amount_due=None
        ) is None
    finally:
        db.close()


# --------------------------------------------------------------------------------------
# API: bulk upload
# --------------------------------------------------------------------------------------


def test_bulk_upload_partial_success(coserv_pdf):
    """A mixed batch: one good import, one in-batch duplicate, one invalid file."""
    good = _bytes(coserv_pdf)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        batch = _bulk(client, [
            ("bill.pdf", good),
            ("bill_copy.pdf", good),          # identical bytes -> duplicate
            ("notes.pdf", b"this is not a pdf"),  # invalid -> failed
        ])

    assert batch["total_files"] == 3
    assert batch["imported_count"] == 1
    assert batch["duplicate_count"] == 1
    assert batch["failed_count"] == 1
    assert batch["status"] == "completed_with_errors"

    by_name = {i["filename"]: i for i in batch["items"]}
    assert by_name["bill.pdf"]["status"] == "imported"
    assert by_name["bill.pdf"]["bill_id"] is not None
    assert by_name["bill_copy.pdf"]["status"] == "duplicate"
    # the duplicate links back to the already-imported bill
    assert by_name["bill_copy.pdf"]["bill_id"] == by_name["bill.pdf"]["bill_id"]
    assert by_name["notes.pdf"]["status"] == "failed"
    assert by_name["notes.pdf"]["error"]


def test_bulk_upload_does_not_abort_on_failure(coserv_pdf):
    """A failing file in the middle must not stop later files from importing."""
    good = _bytes(coserv_pdf)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        batch = _bulk(client, [
            ("bad.pdf", b"garbage"),     # fails first
            ("good.pdf", good),          # must still import
        ])
    assert batch["failed_count"] == 1
    assert batch["imported_count"] == 1


def test_bulk_duplicate_file_upload(coserv_pdf):
    """Re-uploading the exact same file in a later batch is a duplicate, not a new bill."""
    good = _bytes(coserv_pdf)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        first = _bulk(client, [("a.pdf", good)])
        first_id = first["items"][0]["bill_id"]

        second = _bulk(client, [("a.pdf", good)])
        item = second["items"][0]
        assert item["status"] == "duplicate"
        assert item["bill_id"] == first_id
        assert second["imported_count"] == 0

        # exactly one bill stored total
        assert len(client.get("/api/bills").json()) == 1


def test_bulk_same_bill_different_filename(coserv_pdf):
    """Same bytes under a different filename is still detected as a duplicate."""
    good = _bytes(coserv_pdf)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        _bulk(client, [("january.pdf", good)])
        second = _bulk(client, [("renamed.pdf", good)])
        assert second["items"][0]["status"] == "duplicate"


def test_import_batches_endpoints(coserv_pdf):
    good = _bytes(coserv_pdf)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        created = _bulk(client, [("bill.pdf", good)])
        batch_id = created["id"]

        listing = client.get("/api/import-batches").json()
        assert any(b["id"] == batch_id for b in listing)

        detail = client.get(f"/api/import-batches/{batch_id}").json()
        assert detail["id"] == batch_id
        assert detail["total_files"] == 1
        assert detail["items"][0]["filename"] == "bill.pdf"

        assert client.get("/api/import-batches/999999").status_code == 404
