"""End-to-end API test against a real Postgres.

Skipped unless TEST_DATABASE_URL is set. Apply db/001_schema.sql first:

    TEST_DATABASE_URL='postgresql://invoice:invoice@localhost:5434/invoice' \
      pytest tests/test_api_integration.py -v
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

DSN = os.environ.get("TEST_DATABASE_URL")
SAMPLES = Path(__file__).resolve().parents[1] / "samples"

pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is not set")


def test_upload_process_and_deduplicate(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", DSN)
    monkeypatch.setenv("INBOX_DIR", str(tmp_path / "inbox"))
    monkeypatch.setenv("ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setenv("SAMPLE_DIR", str(SAMPLES))
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("LLM_MODE", "mock")

    from invoice_pipeline.config import get_settings

    get_settings.cache_clear()

    from fastapi.testclient import TestClient

    from invoice_pipeline import api, db
    from invoice_pipeline.pipeline import repository
    from invoice_pipeline.pipeline.processor import process_next

    settings = get_settings()
    settings.ensure_dirs()

    with TestClient(api.app) as client:
        repository.reset_demo_data()

        payload = (SAMPLES / "acme_invoice.pdf").read_bytes()
        first = client.post(
            "/api/invoices",
            files={"file": ("acme_invoice.pdf", payload, "application/pdf")},
        )
        assert first.status_code == 202
        invoice_id = first.json()["invoice"]["id"]

        # The API only enqueues; the worker processes. Call it directly here.
        assert process_next(settings) is True

        detail = client.get(f"/api/invoices/{invoice_id}").json()
        invoice = detail["invoice"]
        assert invoice["status"] == "processed"
        assert invoice["supplier_name"] == "Acme Supplies Ltda"
        assert invoice["supplier_cnpj"] == "11222333000181"
        assert invoice["total_amount"] == "1234.56"
        assert invoice["due_date"] == "2026-12-15"
        assert detail["notifications"][0]["status"] == "mock"  # no webhook configured
        assert any(item["action"] == "processed" for item in detail["audit"])

        # Same bytes: no new invoice, no reprocessing.
        second = client.post(
            "/api/invoices",
            files={"file": ("acme_invoice.pdf", payload, "application/pdf")},
        )
        assert second.status_code == 200
        assert second.json()["duplicate"] is True
        assert second.json()["invoice"]["id"] == invoice_id

        # Archived original is downloadable.
        archived = client.get(f"/api/invoices/{invoice_id}/file")
        assert archived.status_code == 200
        assert archived.content.startswith(b"%PDF-")

        # A document missing a required field is routed to human review.
        bad = (SAMPLES / "missing_due_date.pdf").read_bytes()
        third = client.post(
            "/api/invoices",
            files={"file": ("missing_due_date.pdf", bad, "application/pdf")},
        )
        assert third.status_code == 202
        assert process_next(settings) is True
        review = client.get(f"/api/invoices/{third.json()['invoice']['id']}").json()
        assert review["invoice"]["status"] == "needs_review"
        assert review["invoice"]["validation_errors"] == ["missing_due_date"]

        stats = client.get("/api/stats").json()
        assert stats["processed"] == 1
        assert stats["needs_review"] == 1

        # Invalid payloads are rejected at the edge.
        invalid = client.post(
            "/api/invoices",
            files={"file": ("not-a.pdf", b"hello", "application/pdf")},
        )
        assert invalid.status_code == 400
        assert invalid.json()["detail"] == "not a PDF"

    db.close_pool()
    get_settings.cache_clear()
