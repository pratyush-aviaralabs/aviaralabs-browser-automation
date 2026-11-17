from datetime import date

import pytest

from dealercenter_agent.config import DBConfig
from dealercenter_agent.db.engine import get_engine, get_session_factory
from dealercenter_agent.db.models import (
    Base,
    Escalation,
    Invoice,
    Tenant,
    ToolRun,
    Workflow,
    WorkflowRun,
    WorkflowStep,
)


@pytest.fixture
def db_session():
    cfg = DBConfig(_env_file=None, database_url="sqlite:///:memory:")
    engine = get_engine(cfg)
    Base.metadata.create_all(engine)
    SessionFactory = get_session_factory(cfg)
    SessionFactory.configure(bind=engine)
    session = SessionFactory()
    yield session
    session.close()


def test_invoice_round_trip_stores_full_vin(db_session):
    tenant = Tenant(name="Tailgate Motor Co")
    db_session.add(tenant)
    db_session.flush()

    inv = Invoice(
        tenant_id=tenant.id,
        invoice_number="110011350",
        vendor_name="Jensen",
        grand_total=250.00,
        invoice_date=date(2026, 11, 17),
        vin="1FTFW1E81NFA12345",
        status="queued",
        payload_json={},
        callback_url="https://example.com/cb",
    )
    db_session.add(inv)
    db_session.commit()

    fetched = db_session.get(Invoice, inv.id)
    assert fetched.vin == "1FTFW1E81NFA12345"
    assert fetched.status == "queued"
    assert fetched.tenant_id == tenant.id


def test_tool_run_zero_llm_cost_by_default(db_session):
    tenant = Tenant(name="T")
    db_session.add(tenant)
    db_session.flush()
    inv = Invoice(
        tenant_id=tenant.id, invoice_number="X", vendor_name="V", grand_total=1,
        invoice_date=date(2026, 1, 1), status="queued", payload_json={},
        callback_url="https://x",
    )
    db_session.add(inv)
    db_session.flush()

    run = ToolRun(
        invoice_id=inv.id, tool_name="LoginTool", input_json={}, output_json={},
        status="success", duration_ms=120,
    )
    db_session.add(run)
    db_session.commit()

    assert db_session.get(ToolRun, run.id).llm_cost_usd == 0


def test_workflow_step_sequence_is_queryable_and_orderable(db_session):
    wf = Workflow(name="dealercenter_cost_add", version=1)
    db_session.add(wf)
    db_session.flush()

    db_session.add_all([
        WorkflowStep(workflow_id=wf.id, step_order=1, tool_name="LoginTool"),
        WorkflowStep(workflow_id=wf.id, step_order=2, tool_name="SearchInvoiceTool"),
    ])
    db_session.commit()

    steps = (
        db_session.query(WorkflowStep)
        .filter_by(workflow_id=wf.id)
        .order_by(WorkflowStep.step_order)
        .all()
    )
    assert [s.tool_name for s in steps] == ["LoginTool", "SearchInvoiceTool"]


def test_workflow_run_tracks_current_step_for_retry(db_session):
    tenant = Tenant(name="T")
    wf = Workflow(name="dealercenter_cost_add", version=1)
    db_session.add_all([tenant, wf])
    db_session.flush()
    inv = Invoice(
        tenant_id=tenant.id, invoice_number="X", vendor_name="V", grand_total=1,
        invoice_date=date(2026, 1, 1), status="processing", payload_json={},
        callback_url="https://x",
    )
    db_session.add(inv)
    db_session.flush()

    run = WorkflowRun(invoice_id=inv.id, workflow_id=wf.id, status="failed", current_step_order=3)
    db_session.add(run)
    db_session.commit()

    fetched = db_session.get(WorkflowRun, run.id)
    assert fetched.current_step_order == 3
    assert fetched.status == "failed"


def test_escalation_links_to_workflow_run(db_session):
    tenant = Tenant(name="T")
    wf = Workflow(name="wf", version=1)
    db_session.add_all([tenant, wf])
    db_session.flush()
    inv = Invoice(
        tenant_id=tenant.id, invoice_number="X", vendor_name="V", grand_total=1,
        invoice_date=date(2026, 1, 1), status="failed", payload_json={},
        callback_url="https://x",
    )
    db_session.add(inv)
    db_session.flush()
    run = WorkflowRun(invoice_id=inv.id, workflow_id=wf.id, status="escalated")
    db_session.add(run)
    db_session.flush()

    esc = Escalation(invoice_id=inv.id, workflow_run_id=run.id, reason="vendor not found")
    db_session.add(esc)
    db_session.commit()

    assert db_session.get(Escalation, esc.id).workflow_run_id == run.id
