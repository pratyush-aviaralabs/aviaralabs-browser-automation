import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from dealercenter_agent.db.models import Base, Invoice
from dealercenter_agent.ingestion import status as s
from dealercenter_agent.web import app as app_module
from dealercenter_agent.web import webhooks as webhooks_module

SAMPLE_PAYLOAD = {
    "data": {
        "invoice_number": "INV-1042",
        "invoice_date": "2026-11-03",
        "po_number": None,
        "vendor_name": "Hillside Auto Detail LLC",
        "stock_number": "ST-77812",
        "vin": "1HGCM82633A004352",
        "line_items": [
            {"description": "Bumper repair - body detail", "quantity": 1, "unit_price": 220.00, "total": 220.00},
        ],
        "grand_total": 327.88,
    },
    "file_url": "https://blob.example.com/vendor-invoices/INV-1042.pdf?sas=fresh-token",
    "attachments": [{"url": "https://blob.example.com/attachments/INV-1042-copy.pdf?sas=fresh-token-2"}],
    "invoice_id": "665f1c2e9a3b4d0012ab34cd",
    "callback_url": "https://backend.example.com/organization/connectors/dealercenter/callback/abc123signed/",
}


class FakeQueue:
    def __init__(self):
        self.enqueued = []

    def enqueue(self, func, *args, **kwargs):
        self.enqueued.append((func, args, kwargs))


@pytest.fixture
def client(monkeypatch):
    # StaticPool keeps every checkout on the same underlying sqlite
    # :memory: connection — without it, each session the webhook handler
    # and the test itself open would land on a separate, empty in-memory
    # database and "no such table" immediately.
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(webhooks_module, "_SESSION_FACTORY", session_factory)

    fake_queue = FakeQueue()
    monkeypatch.setattr(webhooks_module, "get_invoice_ingestion_queue", lambda: fake_queue)

    test_client = TestClient(app_module.app)
    test_client.fake_queue = fake_queue
    test_client.session_factory = session_factory
    return test_client


def test_webhook_creates_invoice_and_enqueues_ingestion_job(client):
    resp = client.post("/webhooks/invoices", json=SAMPLE_PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "accepted"
    invoice_id = body["invoice_id"]

    with client.session_factory() as session:
        invoice = session.get(Invoice, invoice_id)
        assert invoice.invoice_number == "INV-1042"
        assert invoice.external_invoice_id == "665f1c2e9a3b4d0012ab34cd"
        assert invoice.status == s.RECEIVED
        assert invoice.payload_json["category"] == "Repair"
        assert invoice.payload_json["attachments"] == [
            {"url": "https://blob.example.com/attachments/INV-1042-copy.pdf?sas=fresh-token-2"}
        ]

    assert len(client.fake_queue.enqueued) == 1
    func, args, _kwargs = client.fake_queue.enqueued[0]
    assert func.__name__ == "process_invoice_ingestion"
    assert args == (invoice_id,)


def test_webhook_accepts_a_repeated_invoice_id_as_a_new_row(client):
    # Confirmed required (2026-11-18): duplicate invoices are accepted
    # deliberately — every delivery, including a resend of the exact
    # same invoice_id, creates its own row and gets its own ingestion
    # job. A true duplicate is still caught downstream, at the point
    # that actually matters (CostAddTool's own EXISTS check against
    # DealerCenter's live data), not rejected here.
    first = client.post("/webhooks/invoices", json=SAMPLE_PAYLOAD)
    second = client.post("/webhooks/invoices", json=SAMPLE_PAYLOAD)

    assert first.json()["status"] == "accepted"
    assert second.json()["status"] == "accepted"
    assert second.json()["invoice_id"] != first.json()["invoice_id"]

    with client.session_factory() as session:
        rows = session.query(Invoice).filter_by(external_invoice_id="665f1c2e9a3b4d0012ab34cd").all()
        assert len(rows) == 2

    # Each delivery enqueued its own ingestion job.
    assert len(client.fake_queue.enqueued) == 2


def test_webhook_rolls_back_invoice_row_when_enqueue_fails(client):
    # Confirmed live (2026-11-18): a Redis outage used to leave a zombie
    # "received" row behind, which then permanently absorbed every
    # retry as "duplicate" without ever actually enqueueing. The row
    # must be rolled back so a retry starts clean.
    def broken_enqueue(*_args, **_kwargs):
        raise ConnectionError("Redis down")

    client.fake_queue.enqueue = broken_enqueue

    resp = client.post("/webhooks/invoices", json=SAMPLE_PAYLOAD)
    assert resp.status_code == 503

    with client.session_factory() as session:
        assert session.query(Invoice).count() == 0

    # Fix the queue back up and retry — it must create a fresh row and
    # actually enqueue this time, not silently no-op as a duplicate.
    client.fake_queue.enqueue = lambda func, *a, **kw: client.fake_queue.enqueued.append((func, a, kw))
    retry = client.post("/webhooks/invoices", json=SAMPLE_PAYLOAD)
    assert retry.json()["status"] == "accepted"
    assert len(client.fake_queue.enqueued) == 1


def test_webhook_rejects_payload_missing_required_field(client):
    bad_payload = {**SAMPLE_PAYLOAD, "invoice_id": None}
    resp = client.post("/webhooks/invoices", json=bad_payload)
    assert resp.status_code == 422


def test_callback_status_endpoint_reports_current_status(client):
    create = client.post("/webhooks/invoices", json=SAMPLE_PAYLOAD)
    invoice_id = create.json()["invoice_id"]

    resp = client.get("/organization/connectors/dealercenter/callback/abc123signed/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["invoice_number"] == "INV-1042"
    assert body["status"] == s.RECEIVED
    assert body["invoice_id"] == "665f1c2e9a3b4d0012ab34cd"


def test_callback_status_endpoint_404s_on_unknown_token(client):
    resp = client.get("/organization/connectors/dealercenter/callback/nope/")
    assert resp.status_code == 404


def test_callback_status_endpoint_resolves_to_latest_row_when_duplicated(client):
    first = client.post("/webhooks/invoices", json=SAMPLE_PAYLOAD)
    second = client.post("/webhooks/invoices", json=SAMPLE_PAYLOAD)
    assert first.json()["invoice_id"] != second.json()["invoice_id"]

    with client.session_factory() as session:
        latest = session.get(Invoice, second.json()["invoice_id"])
        latest.status = s.READY_FOR_PROCESSING
        session.commit()

    resp = client.get("/organization/connectors/dealercenter/callback/abc123signed/")
    assert resp.status_code == 200
    assert resp.json()["status"] == s.READY_FOR_PROCESSING
