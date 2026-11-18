"""One-off dev seed: a Workflow definition plus a handful of dummy
WorkflowRun + ToolRun rows, so the Workflow Runs page has traces to show
before any real pipeline run has logged anything. Safe to re-run — it's
a no-op once any workflow_runs rows already exist."""

from datetime import datetime, timedelta, timezone

from dealercenter_agent.config import DBConfig
from dealercenter_agent.db.engine import get_session_factory
from dealercenter_agent.db.models import Invoice, ToolRun, Workflow, WorkflowRun, WorkflowStep

STEP_NAMES = [
    "LoginTool",
    "SearchInvoiceTool",
    "CostAddTool",
    "VerifyCostAddTool",
    "FileUploadTool",
    "VerifyFileUploadTool",
]

# (overall run status, per-step statuses — same order as STEP_NAMES,
# truncated to however many steps actually ran before success/failure)
DUMMY_RUNS = [
    ("success", ["success"] * 6),
    ("success", ["success"] * 6),
    ("failed", ["success", "success", "failed"]),
    ("success", ["success"] * 6),
    ("failed", ["success", "failed"]),
    ("running", ["success", "success", "success"]),
]


def main() -> None:
    session_factory = get_session_factory(DBConfig())
    with session_factory() as session:
        if session.query(WorkflowRun).count() > 0:
            print("workflow_runs table already has rows — skipping seed")
            return

        workflow = session.query(Workflow).filter_by(name="CostAddPipeline").first()
        if workflow is None:
            workflow = Workflow(name="CostAddPipeline", version=1, is_active=True)
            session.add(workflow)
            session.flush()
            for i, tool_name in enumerate(STEP_NAMES):
                session.add(WorkflowStep(workflow_id=workflow.id, step_order=i, tool_name=tool_name))
            session.flush()

        invoices = session.query(Invoice).order_by(Invoice.id).limit(len(DUMMY_RUNS)).all()
        if not invoices:
            print("no invoices to attach dummy workflow runs to — run seed_dummy_invoices.py first")
            return

        now = datetime.now(timezone.utc)
        for i, (invoice, (run_status, step_statuses)) in enumerate(zip(invoices, DUMMY_RUNS)):
            started_at = now - timedelta(hours=len(DUMMY_RUNS) - i)
            run = WorkflowRun(
                invoice_id=invoice.id,
                workflow_id=workflow.id,
                status=run_status,
                current_step_order=len(step_statuses) - 1,
                started_at=started_at,
                finished_at=None if run_status == "running" else started_at + timedelta(seconds=30),
            )
            session.add(run)
            session.flush()

            step_started = started_at
            for step_order, step_status in enumerate(step_statuses):
                duration_ms = 1200 + step_order * 300
                step_finished = step_started + timedelta(milliseconds=duration_ms)
                session.add(
                    ToolRun(
                        invoice_id=invoice.id,
                        workflow_run_id=run.id,
                        tool_name=STEP_NAMES[step_order],
                        input_json={"invoice_number": invoice.invoice_number},
                        output_json={} if step_status == "failed" else {"status": "SUCCESS"},
                        status=step_status,
                        duration_ms=duration_ms,
                        started_at=step_started,
                        finished_at=step_finished,
                    )
                )
                step_started = step_finished
            session.commit()

        print(f"seeded {len(DUMMY_RUNS)} dummy workflow runs")


if __name__ == "__main__":
    main()
