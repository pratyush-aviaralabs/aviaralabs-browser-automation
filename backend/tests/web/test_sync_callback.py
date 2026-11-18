from datetime import date, datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from dealercenter_agent.db.models import Base, Invoice, Tenant, ToolRun, Workflow, WorkflowRun
from dealercenter_agent.ingestion import status as s
from dealercenter_agent.web import app as app_module


def _add_workflow_run(session, invoice: Invoice, tool_runs: list[tuple[str, str, dict]]) -> None:
    workflow = Workflow(name="CostAddPipeline", version=1, is_active=True)
    session.add(workflow)
    session.flush()
    run = WorkflowRun(
        invoice_id=invoice.id, workflow_id=workflow.id, status="success", started_at=datetime.now(timezone.utc)
    )
    session.add(run)
    session.flush()
    for tool_name, status, output_json in tool_runs:
        session.add(
            ToolRun(
                invoice_id=invoice.id,
                workflow_run_id=run.id,
                tool_name=tool_name,
                input_json={},
                output_json=output_json,
                status=status,
                duration_ms=100,
                started_at=datetime.now(timezone.utc),
            )
        )
    session.flush()


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(app_module, "_SESSION_FACTORY", session_factory)

    with session_factory() as session:
        tenant = Tenant(name="Dev Dealer")
        session.add(tenant)
        session.flush()
        for i, status in enumerate([s.PROCESSED, s.FAILED, s.READY_FOR_PROCESSING, s.QUEUED, s.PROCESSED]):
            session.add(
                Invoice(
                    tenant_id=tenant.id,
                    invoice_number=f"INV-{i}",
                    vendor_name="Vendor",
                    grand_total=1,
                    invoice_date=date(2026, 11, 18),
                    status=status,
                    stock_number="22361" if status == s.PROCESSED else None,
                    callback_url="https://example.com/callback/",
                )
            )
        session.commit()

        # Invoice 1: PROCESSED with a confirmed, verified cost-add -> "created".
        _add_workflow_run(
            session,
            session.get(Invoice, 1),
            [("CostAddTool (attempt 1)", "success", {}), ("VerifyCostAddTool (attempt 1)", "success", {})],
        )
        # Invoice 2: FAILED with a real failure reason on the cost-add step.
        _add_workflow_run(
            session, session.get(Invoice, 2), [("CostAddTool (attempt 1)", "failed", {"reason": "not found"})]
        )
        # Invoice 5: PROCESSED but CostAddTool actually reported EXISTS —
        # the exact live bug (2026-11-18): invoice.status alone can't
        # tell this apart from a real "created".
        _add_workflow_run(
            session,
            session.get(Invoice, 5),
            [("CostAddTool (attempt 1)", "exists", {"memo": "INV-4"}), ("FileUploadTool", "exists", {})],
        )
        session.commit()

    test_client = TestClient(app_module.app)
    test_client.session_factory = session_factory
    return test_client


def test_sync_callback_posts_created_for_verified_cost_add(client, monkeypatch):
    calls = []

    class _FakeResponse:
        def raise_for_status(self):
            pass

    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: calls.append(json) or _FakeResponse())

    resp = client.post("/invoices/1/sync-callback")

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": True, "status": "created", "message": "Cost added to stock 22361"}
    assert calls == [{"status": "created", "message": "Cost added to stock 22361"}]


def test_sync_callback_posts_exists_instead_of_false_created(client, monkeypatch):
    """The exact bug reported live: a PROCESSED invoice whose CostAddTool
    actually reported EXISTS must sync as "exists", not a false "created"."""
    calls = []

    class _FakeResponse:
        def raise_for_status(self):
            pass

    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: calls.append(json) or _FakeResponse())

    resp = client.post("/invoices/5/sync-callback")

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": True, "status": "exists", "message": "DealerCenter already had this entry"}
    assert calls == [{"status": "exists", "message": "DealerCenter already had this entry"}]


def test_sync_callback_posts_failed_for_failed_invoice(client, monkeypatch):
    calls = []

    class _FakeResponse:
        def raise_for_status(self):
            pass

    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: calls.append(json) or _FakeResponse())

    resp = client.post("/invoices/2/sync-callback")

    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"
    assert calls[0]["status"] == "failed"


def test_sync_callback_422s_for_non_terminal_status(client, monkeypatch):
    calls = []
    monkeypatch.setattr(httpx, "post", lambda *a, **k: calls.append(1))

    resp = client.post("/invoices/3/sync-callback")

    assert resp.status_code == 422
    assert calls == []


def test_sync_callback_404s_for_unknown_invoice(client):
    resp = client.post("/invoices/999999/sync-callback")
    assert resp.status_code == 404


def test_sync_callback_502s_when_delivery_fails(client, monkeypatch):
    def _raise(*args, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "post", _raise)

    resp = client.post("/invoices/1/sync-callback")

    assert resp.status_code == 502
    assert "boom" in resp.json()["detail"]
