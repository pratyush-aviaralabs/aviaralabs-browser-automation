from datetime import date

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from dealercenter_agent.config import StorageConfig
from dealercenter_agent.db.models import Base, Invoice, Tenant
from dealercenter_agent.ingestion import jobs
from dealercenter_agent.ingestion import status as s


@pytest.fixture
def callback_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: calls.append(json))
    return calls


@pytest.fixture
def db_config(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(jobs, "_SESSION_FACTORY", session_factory)
    monkeypatch.setattr(jobs, "StorageConfig", lambda: StorageConfig(_env_file=None, storage_local_path=tmp_path))
    return None, session_factory


def _make_invoice(
    session_factory,
    file_url="https://blob.example.com/inv.pdf?sas=tok",
    attachments=None,
    external_invoice_id="665f1c2e9a3b4d0012ab34cd",
) -> int:
    with session_factory() as session:
        tenant = Tenant(name="Dev Dealer")
        session.add(tenant)
        session.flush()
        invoice = Invoice(
            tenant_id=tenant.id,
            invoice_number="INV-1042",
            vendor_name="Hillside Auto Detail LLC",
            grand_total=327.88,
            invoice_date=date(2026, 11, 3),
            status=s.RECEIVED,
            external_invoice_id=external_invoice_id,
            callback_url="https://backend.example.com/callback/abc/",
            payload_json={
                "category": "Repair",
                "file_url": file_url,
                "attachments": attachments or [],
            },
        )
        session.add(invoice)
        session.commit()
        return invoice.id


def test_process_invoice_ingestion_downloads_pdf_and_marks_ready(db_config, monkeypatch, tmp_path, callback_calls):
    _cfg, session_factory = db_config
    invoice_id = _make_invoice(session_factory)

    written = {}

    def fake_download(url, dest_path):
        written["url"] = url
        written["dest_path"] = dest_path
        with open(dest_path, "wb") as f:
            f.write(b"%PDF-1.4 fake")

    monkeypatch.setattr(jobs, "_download", fake_download)

    jobs.process_invoice_ingestion(invoice_id)

    with session_factory() as session:
        invoice = session.get(Invoice, invoice_id)
        assert invoice.status == s.READY_FOR_PROCESSING
        assert invoice.local_pdf_path == str(tmp_path / "invoice-665f1c2e9a3b4d0012ab34cd.pdf")
    assert written["url"] == "https://blob.example.com/inv.pdf?sas=tok"
    # Not a terminal DealerCenter outcome yet — no callback fires here;
    # the sender's webhook 2xx already told them it's "Sent".
    assert callback_calls == []


def test_process_invoice_ingestion_marks_failed_on_download_error(db_config, monkeypatch, callback_calls):
    _cfg, session_factory = db_config
    invoice_id = _make_invoice(session_factory)

    def failing_download(url, dest_path):
        raise RuntimeError("blob storage returned 403")

    monkeypatch.setattr(jobs, "_download", failing_download)

    jobs.process_invoice_ingestion(invoice_id)

    with session_factory() as session:
        invoice = session.get(Invoice, invoice_id)
        assert invoice.status == s.FAILED_FILE_STORAGE
        assert "blob storage returned 403" in invoice.payload_json["ingestion_error"]
    assert callback_calls == [{"status": "failed", "message": "blob storage returned 403"}]


def test_process_invoice_ingestion_is_noop_for_unknown_invoice_id(db_config):
    _cfg, session_factory = db_config
    jobs.process_invoice_ingestion(999999)  # must not raise


def test_marks_failed_data_storage_when_ingesting_commit_fails(db_config, monkeypatch, callback_calls):
    _cfg, session_factory = db_config
    invoice_id = _make_invoice(session_factory)

    from sqlalchemy.orm import Session

    real_commit = Session.commit
    call_count = {"n": 0}

    def flaky_commit(self):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("connection reset")
        return real_commit(self)

    monkeypatch.setattr(Session, "commit", flaky_commit)

    jobs.process_invoice_ingestion(invoice_id)

    with session_factory() as session:
        invoice = session.get(Invoice, invoice_id)
        assert invoice.status == s.FAILED_DATA_STORAGE
    assert callback_calls == [
        {"status": "failed", "message": "internal error while saving the invoice record — please resend"}
    ]


def test_falls_back_to_attachment_when_file_url_download_fails(db_config, monkeypatch, tmp_path):
    _cfg, session_factory = db_config
    invoice_id = _make_invoice(
        session_factory,
        file_url="https://blob.example.com/expired.pdf?sas=stale",
        attachments=[{"url": "https://blob.example.com/attachment-1.pdf?sas=fresh"}],
    )

    def selective_download(url, dest_path):
        if "expired" in url:
            raise RuntimeError("signed URL expired")
        with open(dest_path, "wb") as f:
            f.write(b"%PDF-1.4 from attachment")

    monkeypatch.setattr(jobs, "_download", selective_download)

    jobs.process_invoice_ingestion(invoice_id)

    with session_factory() as session:
        invoice = session.get(Invoice, invoice_id)
        assert invoice.status == s.READY_FOR_PROCESSING
        assert invoice.local_pdf_path == str(tmp_path / "invoice-665f1c2e9a3b4d0012ab34cd.pdf")
        assert invoice.payload_json["downloaded_from"] == "attachment[0]"
        assert invoice.payload_json["downloaded_source"] == "https://blob.example.com/attachment-1.pdf?sas=fresh"


def test_tries_every_attachment_before_giving_up(db_config, monkeypatch):
    _cfg, session_factory = db_config
    invoice_id = _make_invoice(
        session_factory,
        file_url="https://blob.example.com/expired.pdf?sas=stale",
        attachments=[
            {"url": "https://blob.example.com/attachment-1.pdf?sas=also-bad"},
            {"url": "https://blob.example.com/attachment-2.pdf?sas=good"},
        ],
    )

    def selective_download(url, dest_path):
        if "attachment-2" not in url:
            raise RuntimeError(f"download failed for {url}")
        with open(dest_path, "wb") as f:
            f.write(b"%PDF-1.4 from second attachment")

    monkeypatch.setattr(jobs, "_download", selective_download)

    jobs.process_invoice_ingestion(invoice_id)

    with session_factory() as session:
        invoice = session.get(Invoice, invoice_id)
        assert invoice.status == s.READY_FOR_PROCESSING
        assert invoice.payload_json["downloaded_from"] == "attachment[1]"


def test_prefers_inline_attachment_content_over_file_url(db_config, monkeypatch, tmp_path):
    import base64

    inline_bytes = b"%PDF-1.4 embedded directly, no download needed"
    invoice_id = _make_invoice(
        db_config[1],
        file_url="https://blob.example.com/inv.pdf?sas=tok",
        attachments=[
            {"content_base64": base64.b64encode(inline_bytes).decode(), "filename": "sample.pdf"}
        ],
    )

    def fail_if_called(url, dest_path):
        raise AssertionError("should not download — inline attachment content takes priority")

    monkeypatch.setattr(jobs, "_download", fail_if_called)

    jobs.process_invoice_ingestion(invoice_id)

    with db_config[1]() as session:
        invoice = session.get(Invoice, invoice_id)
        assert invoice.status == s.READY_FOR_PROCESSING
        dest_path = tmp_path / "invoice-665f1c2e9a3b4d0012ab34cd.pdf"
        assert invoice.local_pdf_path == str(dest_path)
        assert dest_path.read_bytes() == inline_bytes
        assert invoice.payload_json["downloaded_from"] == "attachment[0]_inline"
        assert invoice.payload_json["downloaded_source"] == "sample.pdf"


def test_falls_back_to_url_download_when_no_attachment_has_inline_content(db_config, monkeypatch):
    invoice_id = _make_invoice(
        db_config[1],
        file_url="https://blob.example.com/inv.pdf?sas=tok",
        attachments=[{"url": "https://blob.example.com/attachment-1.pdf?sas=also-fine"}],
    )

    written = {}

    def fake_download(url, dest_path):
        written["url"] = url
        with open(dest_path, "wb") as f:
            f.write(b"%PDF-1.4 downloaded")

    monkeypatch.setattr(jobs, "_download", fake_download)

    jobs.process_invoice_ingestion(invoice_id)

    with db_config[1]() as session:
        invoice = session.get(Invoice, invoice_id)
        assert invoice.status == s.READY_FOR_PROCESSING
        assert invoice.payload_json["downloaded_from"] == "file_url"
    assert written["url"] == "https://blob.example.com/inv.pdf?sas=tok"


def test_falls_through_to_url_when_inline_content_is_malformed(db_config, monkeypatch):
    invoice_id = _make_invoice(
        db_config[1],
        file_url="https://blob.example.com/inv.pdf?sas=tok",
        attachments=[{"content_base64": "not-valid-base64!!!", "filename": "bad.pdf"}],
    )

    def fake_download(url, dest_path):
        with open(dest_path, "wb") as f:
            f.write(b"%PDF-1.4 downloaded after bad inline content")

    monkeypatch.setattr(jobs, "_download", fake_download)

    jobs.process_invoice_ingestion(invoice_id)

    with db_config[1]() as session:
        invoice = session.get(Invoice, invoice_id)
        assert invoice.status == s.READY_FOR_PROCESSING
        assert invoice.payload_json["downloaded_from"] == "file_url"


def test_marks_failed_when_file_url_and_every_attachment_fail(db_config, monkeypatch):
    _cfg, session_factory = db_config
    invoice_id = _make_invoice(
        session_factory,
        file_url="https://blob.example.com/expired.pdf?sas=stale",
        attachments=[{"url": "https://blob.example.com/attachment-1.pdf?sas=also-bad"}],
    )

    def always_fails(url, dest_path):
        raise RuntimeError(f"download failed for {url}")

    monkeypatch.setattr(jobs, "_download", always_fails)

    jobs.process_invoice_ingestion(invoice_id)

    with session_factory() as session:
        invoice = session.get(Invoice, invoice_id)
        assert invoice.status == s.FAILED_FILE_STORAGE
        assert "attachment-1.pdf" in invoice.payload_json["ingestion_error"]
