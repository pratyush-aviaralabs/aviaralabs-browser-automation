from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from dealercenter_agent.db.models import Base, Invoice, Tenant
from dealercenter_agent.ingestion import status as s
from dealercenter_agent.web import app as app_module


class FakeQueue:
    count = 3


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(app_module, "_SESSION_FACTORY", session_factory)
    monkeypatch.setattr(app_module, "get_invoice_ingestion_queue", lambda: FakeQueue())

    with session_factory() as session:
        tenant = Tenant(name="Dev Dealer")
        session.add(tenant)
        session.flush()
        statuses = [s.READY_FOR_PROCESSING, s.READY_FOR_PROCESSING, s.FAILED_FILE_STORAGE, s.RECEIVED]
        for i, status in enumerate(statuses):
            session.add(
                Invoice(
                    tenant_id=tenant.id,
                    invoice_number=f"INV-LOADUI-123-{i:04d}",
                    vendor_name="UI Load Test Vendor",
                    grand_total=1,
                    invoice_date=date(2026, 11, 18),
                    status=status,
                    callback_url="https://example.com/callback/",
                )
            )
        session.add(
            Invoice(
                tenant_id=tenant.id,
                invoice_number="INV-OTHER-1",
                vendor_name="Someone Else",
                grand_total=1,
                invoice_date=date(2026, 11, 18),
                status=s.READY_FOR_PROCESSING,
                callback_url="https://example.com/callback/",
            )
        )
        session.commit()

    return TestClient(app_module.app)


def test_invoices_status_by_prefix_counts_only_matching_rows(client):
    resp = client.get("/invoices/status-by-prefix", params={"prefix": "INV-LOADUI-123-"})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"total": 4, "ready": 2, "failed": 1, "in_flight": 1}


def test_invoices_status_by_prefix_returns_zeros_for_no_match(client):
    resp = client.get("/invoices/status-by-prefix", params={"prefix": "INV-NOPE-"})
    assert resp.json() == {"total": 0, "ready": 0, "failed": 0, "in_flight": 0}


def test_queue_status_reports_depth(client):
    resp = client.get("/queue/status")
    assert resp.status_code == 200
    assert resp.json() == {"depth": 3}


def test_invoices_status_by_prefix_counts_both_failure_statuses(client, monkeypatch):
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(app_module, "_SESSION_FACTORY", session_factory)

    with session_factory() as session:
        tenant = Tenant(name="Dev Dealer")
        session.add(tenant)
        session.flush()
        for i, status in enumerate([s.FAILED_DATA_STORAGE, s.FAILED_FILE_STORAGE]):
            session.add(
                Invoice(
                    tenant_id=tenant.id,
                    invoice_number=f"INV-BOTHFAIL-{i:04d}",
                    vendor_name="Vendor",
                    grand_total=1,
                    invoice_date=date(2026, 11, 18),
                    status=status,
                    callback_url="https://example.com/callback/",
                )
            )
        session.commit()

    client = TestClient(app_module.app)
    resp = client.get("/invoices/status-by-prefix", params={"prefix": "INV-BOTHFAIL-"})
    assert resp.json() == {"total": 2, "ready": 0, "failed": 2, "in_flight": 0}


def test_status_by_prefix_route_is_not_shadowed_by_invoice_id_route(client):
    # Regression: /invoices/{invoice_id} is registered elsewhere with an
    # int path param — if it came first, "status-by-prefix" would match
    # that pattern, fail int conversion, and 422 before this route ever
    # got a chance.
    resp = client.get("/invoices/status-by-prefix", params={"prefix": "INV-LOADUI-123-"})
    assert resp.status_code == 200
