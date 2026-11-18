"""Tests for /run's two ways of getting a PDF: an upload, or a reference
to a file ingestion already downloaded (existing_pdf_path). Mocks
run_pipeline out entirely — this must never start a real Playwright
session or touch the real DealerCenter site, in tests or otherwise."""

import threading

import pytest
from fastapi.testclient import TestClient

from dealercenter_agent.web import app as app_module

FORM_FIELDS = {
    "vendor_name": "Hillside Auto Detail LLC",
    "invoice_number": "INV-1042",
    "category": "Repair",
    "cost": "100.00",
    "invoice_date": "2026-11-18",
}


class ImmediateThread:
    """Stands in for threading.Thread so run_pipeline's kwargs are
    captured synchronously instead of actually running anything."""

    last_kwargs = None

    def __init__(self, target, kwargs, daemon=None):
        ImmediateThread.last_kwargs = kwargs

    def start(self):
        pass


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "StorageConfig", lambda: _FakeStorageConfig(tmp_path))
    monkeypatch.setattr(app_module, "threading", _FakeThreadingModule())
    app_module._ACTIVE_RUN_ID = None
    yield TestClient(app_module.app)
    app_module._ACTIVE_RUN_ID = None


class _FakeStorageConfig:
    def __init__(self, path):
        self.storage_local_path = path


class _FakeThreadingModule:
    Thread = ImmediateThread
    Event = threading.Event


def test_run_accepts_uploaded_pdf(client):
    resp = client.post(
        "/run",
        data=FORM_FIELDS,
        files={"pdf": ("invoice.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 200
    assert ImmediateThread.last_kwargs["local_pdf_path"].endswith(".pdf")


def test_run_accepts_existing_pdf_path_already_in_storage(client, tmp_path):
    existing = tmp_path / "invoice-abc123.pdf"
    existing.write_bytes(b"%PDF-1.4 already downloaded")

    resp = client.post("/run", data={**FORM_FIELDS, "existing_pdf_path": str(existing)})
    assert resp.status_code == 200
    assert ImmediateThread.last_kwargs["local_pdf_path"] == str(existing)


def test_run_rejects_existing_pdf_path_outside_storage_dir(client, tmp_path):
    outside = tmp_path.parent / "not-in-storage.pdf"
    outside.write_bytes(b"%PDF-1.4 escaped")

    resp = client.post("/run", data={**FORM_FIELDS, "existing_pdf_path": str(outside)})
    assert resp.status_code == 422


def test_run_rejects_existing_pdf_path_that_does_not_exist(client, tmp_path):
    resp = client.post(
        "/run", data={**FORM_FIELDS, "existing_pdf_path": str(tmp_path / "nope.pdf")}
    )
    assert resp.status_code == 422


def test_run_requires_pdf_or_existing_pdf_path(client):
    resp = client.post("/run", data=FORM_FIELDS)
    assert resp.status_code == 422


def test_uploaded_pdf_takes_priority_over_existing_pdf_path(client, tmp_path):
    existing = tmp_path / "invoice-abc123.pdf"
    existing.write_bytes(b"%PDF-1.4 already downloaded")

    resp = client.post(
        "/run",
        data={**FORM_FIELDS, "existing_pdf_path": str(existing)},
        files={"pdf": ("override.pdf", b"%PDF-1.4 manually picked", "application/pdf")},
    )
    assert resp.status_code == 200
    assert ImmediateThread.last_kwargs["local_pdf_path"] != str(existing)
