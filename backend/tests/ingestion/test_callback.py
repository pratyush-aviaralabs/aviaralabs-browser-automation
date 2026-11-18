from datetime import date, datetime, timezone

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from dealercenter_agent.db.models import Base, Invoice, Tenant, ToolRun, Workflow, WorkflowRun
from dealercenter_agent.ingestion.callback import (
    dealercenter_result_for_invoice,
    notify_dealercenter_result,
    sync_dealercenter_result,
)


def _invoice(**overrides) -> Invoice:
    defaults = dict(
        tenant_id=1,
        invoice_number="INV-1",
        vendor_name="Acme",
        grand_total=100,
        invoice_date=date(2026, 11, 18),
        status="READY_FOR_PROCESSING",
        external_invoice_id="ext-1",
        callback_url="https://example.com/callback/ext-1/",
    )
    defaults.update(overrides)
    return Invoice(**defaults)


def test_notify_posts_exact_contract_shape(monkeypatch):
    calls = []
    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: calls.append((url, json, timeout)))

    invoice = _invoice()
    notify_dealercenter_result(invoice, status="created", message="Cost added to stock 21234")

    assert len(calls) == 1
    url, payload, timeout = calls[0]
    assert url == "https://example.com/callback/ext-1/"  # posted exactly as given, trailing slash kept
    assert payload == {"status": "created", "message": "Cost added to stock 21234"}
    assert timeout == 5.0


def test_notify_omits_optional_fields_when_not_given(monkeypatch):
    calls = []
    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: calls.append(json))

    notify_dealercenter_result(_invoice(), status="exists")

    assert calls[0] == {"status": "exists"}


def test_notify_includes_reference_when_given(monkeypatch):
    calls = []
    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: calls.append(json))

    notify_dealercenter_result(_invoice(), status="created", reference="DC-48213", message="done")

    assert calls[0] == {"status": "created", "reference": "DC-48213", "message": "done"}


def test_notify_truncates_overlong_message_and_reference(monkeypatch):
    calls = []
    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: calls.append(json))

    notify_dealercenter_result(_invoice(), status="failed", message="x" * 2000, reference="y" * 400)

    assert len(calls[0]["message"]) == 1000
    assert len(calls[0]["reference"]) == 200


def test_notify_rejects_unknown_status():
    with pytest.raises(ValueError, match="status must be one of"):
        notify_dealercenter_result(_invoice(), status="bogus")


def test_notify_skips_when_no_callback_url(monkeypatch):
    calls = []
    monkeypatch.setattr(httpx, "post", lambda *a, **k: calls.append(1))

    notify_dealercenter_result(_invoice(callback_url=None), status="failed", message="x")

    assert calls == []


def test_notify_swallows_errors(monkeypatch):
    def _raise(*args, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "post", _raise)

    notify_dealercenter_result(_invoice(), status="failed", message="x")  # must not raise


def test_sync_posts_and_reports_success(monkeypatch):
    calls = []

    class _FakeResponse:
        def raise_for_status(self):
            pass

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)

    result = sync_dealercenter_result(_invoice(), status="created", message="Cost added to stock 21234")

    assert result.ok is True
    assert result.error is None
    assert calls[0][1] == {"status": "created", "message": "Cost added to stock 21234"}


def test_sync_reports_failure_instead_of_swallowing(monkeypatch):
    def _raise(*args, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "post", _raise)

    result = sync_dealercenter_result(_invoice(), status="failed", message="x")

    assert result.ok is False
    assert "boom" in result.error


def test_sync_reports_failure_when_no_callback_url(monkeypatch):
    calls = []
    monkeypatch.setattr(httpx, "post", lambda *a, **k: calls.append(1))

    result = sync_dealercenter_result(_invoice(callback_url=None), status="failed", message="x")

    assert result.ok is False
    assert "callback_url" in result.error
    assert calls == []


# --- dealercenter_result_for_invoice: reconstructed from real ToolRun history ---


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_invoice(session, **overrides) -> Invoice:
    tenant = Tenant(name="Dev Dealer")
    session.add(tenant)
    session.flush()
    defaults = dict(
        tenant_id=tenant.id,
        invoice_number="110011306",
        vendor_name="Acme",
        grand_total=208.56,
        invoice_date=date(2026, 11, 18),
        status="PROCESSED",
        external_invoice_id="ext-1",
        callback_url="https://example.com/callback/ext-1/",
    )
    defaults.update(overrides)
    invoice = Invoice(**defaults)
    session.add(invoice)
    session.flush()
    return invoice


def _seed_workflow_run(session, invoice: Invoice, tool_run_statuses: list[tuple[str, str, dict]]) -> WorkflowRun:
    workflow = Workflow(name="CostAddPipeline", version=1, is_active=True)
    session.add(workflow)
    session.flush()
    run = WorkflowRun(
        invoice_id=invoice.id, workflow_id=workflow.id, status="success", started_at=datetime.now(timezone.utc)
    )
    session.add(run)
    session.flush()
    for tool_name, status, output_json in tool_run_statuses:
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
    return run


def test_dealercenter_result_for_invoice_reconstructs_exists_not_created(db):
    """The exact bug reported live (2026-11-18, invoice 110011306):
    CostAddTool reported EXISTS, but the manual sync button sent
    "created" because it only looked at invoice.status (PROCESSED),
    which can't distinguish the two cases. Must now report "exists"."""
    with db() as session:
        invoice = _seed_invoice(session, status="PROCESSED", stock_number="22361")
        _seed_workflow_run(
            session,
            invoice,
            [
                ("CostAddTool (attempt 1)", "exists", {"memo": "110011306"}),
                ("FileUploadTool", "exists", {}),
            ],
        )
        result = dealercenter_result_for_invoice(session, invoice)

    assert result == ("exists", "DealerCenter already had this entry")


def test_dealercenter_result_for_invoice_reconstructs_created_when_verified(db):
    with db() as session:
        invoice = _seed_invoice(session, status="PROCESSED", stock_number="22361")
        _seed_workflow_run(
            session,
            invoice,
            [
                ("CostAddTool (attempt 1)", "success", {}),
                ("VerifyCostAddTool (attempt 1)", "success", {}),
            ],
        )
        result = dealercenter_result_for_invoice(session, invoice)

    assert result == ("created", "Cost added to stock 22361")


def test_dealercenter_result_for_invoice_reports_failed_when_never_verified(db):
    with db() as session:
        invoice = _seed_invoice(session, status="PROCESSED_BUT_NEED_TO_VALIDATE")
        _seed_workflow_run(
            session,
            invoice,
            [
                ("CostAddTool (attempt 1)", "success", {}),
                ("VerifyCostAddTool (attempt 1)", "failed", {"reason": "not found in grid"}),
            ],
        )
        status, message = dealercenter_result_for_invoice(session, invoice)

    assert status == "failed"
    assert "could not confirm" in message


def test_dealercenter_result_for_invoice_uses_latest_attempt_on_retry(db):
    """CostAddTool retried: attempt 1 was EXISTS... no wait, a more
    realistic retry is attempt 1 unconfirmed success, attempt 2 finds
    the just-added memo and reports EXISTS — the latest attempt (2)
    must win, not the first."""
    with db() as session:
        invoice = _seed_invoice(session, status="PROCESSED")
        _seed_workflow_run(
            session,
            invoice,
            [
                ("CostAddTool (attempt 1)", "success", {}),
                ("VerifyCostAddTool (attempt 1)", "failed", {"reason": "not found in grid"}),
                ("CostAddTool (attempt 2)", "exists", {"memo": "110011306"}),
            ],
        )
        result = dealercenter_result_for_invoice(session, invoice)

    assert result == ("exists", "DealerCenter already had this entry")


def test_dealercenter_result_for_invoice_reports_failed_with_reason_when_cost_add_failed(db):
    with db() as session:
        invoice = _seed_invoice(session, status="FAILED")
        _seed_workflow_run(
            session,
            invoice,
            [("CostAddTool (attempt 1)", "failed", {"reason": "Cost Adds modal did not open"})],
        )
        result = dealercenter_result_for_invoice(session, invoice)

    assert result == ("failed", "Cost Adds modal did not open")


def test_dealercenter_result_for_invoice_falls_back_when_no_tool_run_history(db):
    """Defensive: a terminal status with no WorkflowRun/ToolRun rows at
    all (shouldn't happen for a real workflow-produced status) must
    still return a conservative "failed" instead of crashing or
    silently returning None."""
    with db() as session:
        invoice = _seed_invoice(session, status="PROCESSED")
        status, _message = dealercenter_result_for_invoice(session, invoice)

    assert status == "failed"


def test_dealercenter_result_for_invoice_uses_workflow_error_when_present(db):
    with db() as session:
        invoice = _seed_invoice(session, status="FAILED", payload_json={"workflow_error": "could not start — lock held"})
        result = dealercenter_result_for_invoice(session, invoice)

    assert result == ("failed", "could not start — lock held")


def test_dealercenter_result_for_invoice_uses_ingestion_error_when_present(db):
    with db() as session:
        invoice = _seed_invoice(
            session, status="FAILED_FILE_STORAGE", payload_json={"ingestion_error": "SAS token expired"}
        )
        result = dealercenter_result_for_invoice(session, invoice)

    assert result == ("failed", "SAS token expired")


def test_dealercenter_result_for_invoice_returns_none_for_non_terminal_status(db):
    with db() as session:
        for status in ["RECEIVED", "INGESTING", "READY_FOR_PROCESSING", "QUEUED", "RUNNING"]:
            invoice = _seed_invoice(session, status=status, external_invoice_id=f"ext-{status}")
            assert dealercenter_result_for_invoice(session, invoice) is None
