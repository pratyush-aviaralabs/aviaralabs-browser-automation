from datetime import date

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from dealercenter_agent.db.models import Base, Invoice, Tenant, ToolRun, WorkflowRun
from dealercenter_agent.ingestion import status as s
from dealercenter_agent.tools.base import ToolResult, ToolStatus
from dealercenter_agent.workflow import jobs


@pytest.fixture
def callback_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: calls.append(json))
    return calls


class _FakeDriverContext:
    def __enter__(self):
        return "fake-driver"

    def __exit__(self, *args):
        return False


class _FakeTool:
    """Stands in for any Tool class — .run() ignores args and returns a
    canned ToolResult (or raises) per the class attribute set per test."""

    result: ToolResult | None = None
    error: Exception | None = None

    def run(self, *args, **kwargs):
        if self.error is not None:
            raise self.error
        return self.result


def _make_tool_class(result=None, error=None):
    return type("_FakeTool", (_FakeTool,), {"result": result, "error": error})


@pytest.fixture
def db(monkeypatch, tmp_path):
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(jobs, "_SESSION_FACTORY", session_factory)
    monkeypatch.setattr(jobs, "create_playwright_driver", lambda **kwargs: _FakeDriverContext())
    monkeypatch.setattr(jobs, "_load_saved_state", lambda: None)
    monkeypatch.setattr(jobs, "try_acquire_session_lock", lambda: "token")
    monkeypatch.setattr(jobs, "release_session_lock", lambda token: None)
    monkeypatch.setenv("DEALER_CENTER_USERNAME", "user")
    monkeypatch.setenv("DEALER_CENTER_PASSWORD", "pass")
    return session_factory


def _make_invoice(session_factory, local_pdf_path="/tmp/fake.pdf") -> int:
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
            vin="1HGCM82633A004352",
            status=s.READY_FOR_PROCESSING,
            callback_url="https://example.com/callback/",
            local_pdf_path=local_pdf_path,
            payload_json={"category": "Repair"},
        )
        session.add(invoice)
        session.commit()
        return invoice.id


def _success(data=None):
    return ToolResult(status=ToolStatus.SUCCESS, data=data or {})


def test_full_success_marks_invoice_processed(db, monkeypatch, callback_calls):
    invoice_id = _make_invoice(db)
    monkeypatch.setattr(jobs, "LoginTool", _make_tool_class(_success()))
    monkeypatch.setattr(jobs, "SearchInvoiceTool", _make_tool_class(_success()))
    monkeypatch.setattr(jobs, "CostAddTool", _make_tool_class(_success()))
    monkeypatch.setattr(jobs, "VerifyCostAddTool", _make_tool_class(_success()))
    monkeypatch.setattr(jobs, "FileUploadTool", _make_tool_class(_success({"uploaded_as": "invoice.pdf"})))
    monkeypatch.setattr(jobs, "VerifyFileUploadTool", _make_tool_class(_success()))

    jobs.process_workflow_run(invoice_id)

    with db() as session:
        invoice = session.get(Invoice, invoice_id)
        assert invoice.status == s.PROCESSED
        run = session.scalars(select(WorkflowRun).where(WorkflowRun.invoice_id == invoice_id)).one()
        assert run.status == "success"
        assert run.finished_at is not None
        steps = session.scalars(select(ToolRun).where(ToolRun.workflow_run_id == run.id)).all()
        assert [t.tool_name for t in steps] == [
            "LoginTool",
            "SearchInvoiceTool",
            "CostAddTool (attempt 1)",
            "VerifyCostAddTool (attempt 1)",
            "FileUploadTool",
            "VerifyFileUploadTool",
        ]
        assert all(t.status == "success" for t in steps)

    # No stock_number on this fixture invoice -> falls back to vin.
    assert callback_calls == [{"status": "created", "message": "Cost added to stock 1HGCM82633A004352"}]


def test_cost_add_exists_reports_exists_to_callback(db, monkeypatch, callback_calls):
    invoice_id = _make_invoice(db)
    monkeypatch.setattr(jobs, "LoginTool", _make_tool_class(_success()))
    monkeypatch.setattr(jobs, "SearchInvoiceTool", _make_tool_class(_success()))
    monkeypatch.setattr(jobs, "CostAddTool", _make_tool_class(ToolResult(status=ToolStatus.EXISTS, data={})))
    monkeypatch.setattr(jobs, "FileUploadTool", _make_tool_class(_success({"uploaded_as": "invoice.pdf"})))
    monkeypatch.setattr(jobs, "VerifyFileUploadTool", _make_tool_class(_success()))

    jobs.process_workflow_run(invoice_id)

    assert callback_calls == [{"status": "exists", "message": "DealerCenter already had this entry"}]


def test_login_failure_marks_invoice_failed_and_stops(db, monkeypatch, callback_calls):
    invoice_id = _make_invoice(db)
    monkeypatch.setattr(jobs, "LoginTool", _make_tool_class(error=RuntimeError("login did not succeed")))

    jobs.process_workflow_run(invoice_id)

    with db() as session:
        invoice = session.get(Invoice, invoice_id)
        assert invoice.status == s.FAILED
        run = session.scalars(select(WorkflowRun).where(WorkflowRun.invoice_id == invoice_id)).one()
        assert run.status == "failed"
        steps = session.scalars(select(ToolRun).where(ToolRun.workflow_run_id == run.id)).all()
        assert [t.tool_name for t in steps] == ["LoginTool"]
        assert steps[0].status == "failed"

    assert callback_calls == [
        {"status": "failed", "message": "LoginTool: unexpected error: login did not succeed"}
    ]


def test_search_not_found_marks_invoice_failed(db, monkeypatch, callback_calls):
    invoice_id = _make_invoice(db)
    monkeypatch.setattr(jobs, "LoginTool", _make_tool_class(_success()))
    from dealercenter_agent.tools.base import StepFailure

    monkeypatch.setattr(
        jobs, "SearchInvoiceTool", _make_tool_class(error=StepFailure("SearchInvoiceTool", "not found in inventory"))
    )

    jobs.process_workflow_run(invoice_id)

    with db() as session:
        invoice = session.get(Invoice, invoice_id)
        assert invoice.status == s.FAILED

    assert callback_calls == [{"status": "failed", "message": "SearchInvoiceTool: not found in inventory"}]


def test_cost_add_succeeds_but_verify_never_confirms_needs_validation(db, monkeypatch, callback_calls):
    invoice_id = _make_invoice(db)
    monkeypatch.setattr(jobs, "LoginTool", _make_tool_class(_success()))
    monkeypatch.setattr(jobs, "SearchInvoiceTool", _make_tool_class(_success()))
    monkeypatch.setattr(jobs, "CostAddTool", _make_tool_class(_success()))
    monkeypatch.setattr(
        jobs, "VerifyCostAddTool", _make_tool_class(ToolResult(status=ToolStatus.FAILED, reason="not found in grid"))
    )
    monkeypatch.setattr(jobs, "FileUploadTool", _make_tool_class(_success({"uploaded_as": "invoice.pdf"})))
    monkeypatch.setattr(jobs, "VerifyFileUploadTool", _make_tool_class(_success()))

    jobs.process_workflow_run(invoice_id)

    with db() as session:
        invoice = session.get(Invoice, invoice_id)
        assert invoice.status == s.PROCESSED_BUT_NEED_TO_VALIDATE
        run = session.scalars(select(WorkflowRun).where(WorkflowRun.invoice_id == invoice_id)).one()
        # Ran to completion without crashing — just couldn't confirm a step.
        assert run.status == "success"

    # Unconfirmed save after exhausting retries -> reported as failed to the
    # external callback, even though invoice.status itself is the more
    # nuanced PROCESSED_BUT_NEED_TO_VALIDATE for our own UI.
    assert callback_calls == [
        {
            "status": "failed",
            "message": (
                "could not confirm the cost was saved in DealerCenter after multiple attempts — "
                "please check manually"
            ),
        }
    ]


def test_cost_add_retries_once_on_verify_failure(db, monkeypatch):
    invoice_id = _make_invoice(db)
    monkeypatch.setattr(jobs, "LoginTool", _make_tool_class(_success()))
    monkeypatch.setattr(jobs, "SearchInvoiceTool", _make_tool_class(_success()))
    monkeypatch.setattr(jobs, "CostAddTool", _make_tool_class(_success()))
    monkeypatch.setattr(
        jobs, "VerifyCostAddTool", _make_tool_class(ToolResult(status=ToolStatus.FAILED, reason="not found in grid"))
    )
    monkeypatch.setattr(jobs, "FileUploadTool", _make_tool_class(_success({"uploaded_as": "invoice.pdf"})))
    monkeypatch.setattr(jobs, "VerifyFileUploadTool", _make_tool_class(_success()))

    jobs.process_workflow_run(invoice_id)

    with db() as session:
        run = session.scalars(select(WorkflowRun).where(WorkflowRun.invoice_id == invoice_id)).one()
        steps = session.scalars(select(ToolRun).where(ToolRun.workflow_run_id == run.id)).all()
        cost_add_steps = [t.tool_name for t in steps if t.tool_name.startswith("CostAddTool")]
        assert cost_add_steps == ["CostAddTool (attempt 1)", "CostAddTool (attempt 2)"]


def test_no_local_pdf_path_skips_upload_and_needs_validation(db, monkeypatch, callback_calls):
    invoice_id = _make_invoice(db, local_pdf_path=None)
    monkeypatch.setattr(jobs, "LoginTool", _make_tool_class(_success()))
    monkeypatch.setattr(jobs, "SearchInvoiceTool", _make_tool_class(_success()))
    monkeypatch.setattr(jobs, "CostAddTool", _make_tool_class(_success()))
    monkeypatch.setattr(jobs, "VerifyCostAddTool", _make_tool_class(_success()))

    jobs.process_workflow_run(invoice_id)

    with db() as session:
        invoice = session.get(Invoice, invoice_id)
        assert invoice.status == s.PROCESSED_BUT_NEED_TO_VALIDATE
        run = session.scalars(select(WorkflowRun).where(WorkflowRun.invoice_id == invoice_id)).one()
        steps = session.scalars(select(ToolRun).where(ToolRun.workflow_run_id == run.id)).all()
        assert not any(t.tool_name == "FileUploadTool" for t in steps)

    # The cost itself was confirmed added — a missing/unverified file upload
    # (our own PROCESSED_BUT_NEED_TO_VALIDATE concern) doesn't flip the
    # external callback, since their contract is specifically about the
    # cost-add result.
    assert callback_calls == [{"status": "created", "message": "Cost added to stock 1HGCM82633A004352"}]


def test_mfa_challenge_in_headless_mode_fails_fast(db, monkeypatch):
    invoice_id = _make_invoice(db)

    class _MfaChallengingLoginTool(_FakeTool):
        def run(self, driver, mfa_code_provider, **kwargs):
            mfa_code_provider()  # headless provider must raise here
            raise AssertionError("should never reach this line")

    monkeypatch.setattr(jobs, "LoginTool", _MfaChallengingLoginTool)

    jobs.process_workflow_run(invoice_id)

    with db() as session:
        invoice = session.get(Invoice, invoice_id)
        assert invoice.status == s.FAILED
        run = session.scalars(select(WorkflowRun).where(WorkflowRun.invoice_id == invoice_id)).one()
        steps = session.scalars(select(ToolRun).where(ToolRun.workflow_run_id == run.id)).all()
        assert "Login / Refresh Session" in steps[0].output_json["reason"]


def test_session_lock_already_held_marks_invoice_failed_without_workflow_run(db, monkeypatch, callback_calls):
    invoice_id = _make_invoice(db)
    monkeypatch.setattr(jobs, "try_acquire_session_lock", lambda: None)

    jobs.process_workflow_run(invoice_id)

    with db() as session:
        invoice = session.get(Invoice, invoice_id)
        assert invoice.status == s.FAILED
        assert "session was active" in invoice.payload_json["workflow_error"]
        runs = session.scalars(select(WorkflowRun).where(WorkflowRun.invoice_id == invoice_id)).all()
        assert runs == []

    assert callback_calls == [
        {"status": "failed", "message": "could not start — another DealerCenter session was active"}
    ]


def test_unknown_invoice_id_is_a_noop(db):
    jobs.process_workflow_run(999999)  # must not raise
