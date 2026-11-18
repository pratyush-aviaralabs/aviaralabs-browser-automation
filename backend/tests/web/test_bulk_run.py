from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from dealercenter_agent.db.models import Base, Invoice, Tenant
from dealercenter_agent.ingestion import status as s
from dealercenter_agent.web import app as app_module


class FakeWorkflowQueue:
    def __init__(self):
        self.enqueued = []

    def enqueue(self, func, *args, **kwargs):
        self.enqueued.append((func, args, kwargs))


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(app_module, "_SESSION_FACTORY", session_factory)

    fake_queue = FakeWorkflowQueue()
    monkeypatch.setattr(app_module, "get_workflow_run_queue", lambda: fake_queue)

    with session_factory() as session:
        tenant = Tenant(name="Dev Dealer")
        session.add(tenant)
        session.flush()
        for i, status in enumerate([s.READY_FOR_PROCESSING, s.FAILED, s.QUEUED, s.RUNNING]):
            session.add(
                Invoice(
                    tenant_id=tenant.id,
                    invoice_number=f"INV-{i}",
                    vendor_name="Vendor",
                    grand_total=1,
                    invoice_date=date(2026, 11, 18),
                    status=status,
                    callback_url="https://example.com/callback/",
                )
            )
        session.commit()

    test_client = TestClient(app_module.app)
    test_client.fake_queue = fake_queue
    test_client.session_factory = session_factory
    return test_client


def test_bulk_run_queues_eligible_invoices_and_enqueues_jobs(client):
    resp = client.post("/invoices/bulk-run", json={"invoice_ids": [1, 2]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["queued"] == [1, 2]
    assert body["skipped"] == []

    with client.session_factory() as session:
        assert session.get(Invoice, 1).status == s.QUEUED
        assert session.get(Invoice, 2).status == s.QUEUED

    assert len(client.fake_queue.enqueued) == 2
    for func, args, _kwargs in client.fake_queue.enqueued:
        assert func.__name__ == "process_workflow_run"
    assert {args[0] for _func, args, _kwargs in client.fake_queue.enqueued} == {1, 2}


def test_bulk_run_skips_already_queued_or_running_invoices(client):
    resp = client.post("/invoices/bulk-run", json={"invoice_ids": [3, 4]})
    body = resp.json()
    assert body["queued"] == []
    assert {entry["id"] for entry in body["skipped"]} == {3, 4}
    assert len(client.fake_queue.enqueued) == 0


def test_bulk_run_skips_unknown_invoice_ids(client):
    resp = client.post("/invoices/bulk-run", json={"invoice_ids": [999]})
    body = resp.json()
    assert body["queued"] == []
    assert body["skipped"] == [{"id": 999, "reason": "not found"}]


def test_bulk_run_mixes_queueable_and_skipped_in_one_request(client):
    resp = client.post("/invoices/bulk-run", json={"invoice_ids": [1, 3]})
    body = resp.json()
    assert body["queued"] == [1]
    assert body["skipped"] == [{"id": 3, "reason": f"already {s.QUEUED}"}]


def test_bulk_run_rejects_empty_invoice_ids(client):
    resp = client.post("/invoices/bulk-run", json={"invoice_ids": []})
    assert resp.status_code == 422
