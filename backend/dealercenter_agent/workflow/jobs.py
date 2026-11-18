"""RQ job: runs the CostAdd pipeline (Login -> Search -> CostAdd ->
Verify -> FileUpload -> Verify) against one invoice, headless — no
screencast, no interactive loop, no live WebSocket. Confirmed required
(2026-11-18): this is phase 2/3's queued bulk-run feature, processing
one invoice at a time (SimpleWorker, same reasoning as ingestion's
worker) instead of a human clicking Run on the Live Run page per
invoice.

Unlike the interactive pipeline, this never handles MFA — there's no
human watching a headless job. If the saved session has expired and
DealerCenter challenges for MFA, the job fails fast with a clear reason
pointing at "Login / Refresh Session" rather than hanging forever
waiting for a code nobody can supply."""

import os
import time
from datetime import datetime, timezone

from ..automation.playwright_driver import create_playwright_driver
from ..config import DBConfig
from ..db.engine import get_session_factory
from ..db.models import Invoice, ToolRun, Workflow, WorkflowRun, WorkflowStep
from ..ingestion import status as s
from ..ingestion.callback import notify_dealercenter_result
from ..queue.session_lock import release_session_lock, try_acquire_session_lock
from ..tools.base import StepFailure, ToolStatus
from ..tools.cost_add_tool import CostAddTool
from ..tools.file_upload_tool import FileUploadTool
from ..tools.login_tool import LoginTool
from ..tools.search_invoice_tool import SearchInvoiceTool
from ..tools.verify_cost_add_tool import VerifyCostAddTool
from ..tools.verify_file_upload_tool import VerifyFileUploadTool
from ..web.runner import MAX_SAVE_ATTEMPTS, _load_saved_state

# Confirmed live (2026-11-18): building a fresh session_factory() per
# job call forces a cold TCP+TLS reconnect to Supabase every time
# (~4s) — see ingestion/jobs.py for the full story. Built once per
# worker process, reused across every job that worker processes.
_SESSION_FACTORY = get_session_factory(DBConfig())

WORKFLOW_NAME = "CostAddPipeline"
WORKFLOW_STEP_NAMES = [
    "LoginTool",
    "SearchInvoiceTool",
    "CostAddTool",
    "VerifyCostAddTool",
    "FileUploadTool",
    "VerifyFileUploadTool",
]


def _no_human_mfa_provider() -> str:
    raise StepFailure(
        "LoginTool",
        "MFA challenge — no human available in headless mode. Run 'Login / Refresh Session' on "
        "the Live Run page first, then retry this invoice.",
    )


def _get_or_create_workflow(session) -> Workflow:
    workflow = session.query(Workflow).filter_by(name=WORKFLOW_NAME).first()
    if workflow is not None:
        return workflow

    workflow = Workflow(name=WORKFLOW_NAME, version=1, is_active=True)
    session.add(workflow)
    session.flush()
    for i, tool_name in enumerate(WORKFLOW_STEP_NAMES):
        session.add(WorkflowStep(workflow_id=workflow.id, step_order=i, tool_name=tool_name))
    session.flush()
    return workflow


def _tool_status_label(status: ToolStatus) -> str:
    return status.name.lower()


def _dealercenter_result(
    invoice: Invoice, tracker: "_RunTracker", cost_add_result, cost_verified: bool
) -> tuple[str, str]:
    """Maps this run's outcome onto Aviara Connect's 3-value callback
    contract (created/exists/failed) — judged purely by the cost-add
    step, since that's what their own contract describes ("Cost added
    to stock X"). A FileUploadTool-only hiccup (already reflected in
    invoice.status as PROCESSED_BUT_NEED_TO_VALIDATE) doesn't flip
    this — the cost itself was confirmed added either way.

    Confirmed (2026-11-18): if CostAddTool reports SUCCESS repeatedly
    but VerifyCostAddTool never confirms it, that's reported as
    "failed" rather than trusting the unconfirmed SUCCESS — safe
    either way, since a resend hits CostAddTool's own duplicate check
    and comes back "exists" instead of double-adding."""
    if tracker.failed:
        return "failed", tracker.failure_reason or "the workflow failed for an unknown reason"

    if cost_add_result is not None and cost_add_result.status == ToolStatus.EXISTS:
        return "exists", "DealerCenter already had this entry"

    if cost_add_result is not None and cost_add_result.status == ToolStatus.SUCCESS:
        if cost_verified:
            target = invoice.stock_number or invoice.vin or invoice.invoice_number
            return "created", f"Cost added to stock {target}"
        return (
            "failed",
            "could not confirm the cost was saved in DealerCenter after multiple attempts — please check manually",
        )

    if cost_add_result is not None and cost_add_result.status == ToolStatus.FAILED:
        return "failed", cost_add_result.reason or "could not add the cost in DealerCenter"

    return "failed", "pipeline did not reach the Cost Add step"


class _RunTracker:
    """Times each tool call and writes a ToolRun row for it — the
    persisted equivalent of runner.py's _run_step, which only emits
    over a WebSocket. Tracks whether anything needs validation (a
    Verify* step that never confirmed) or failed outright, so the
    caller can map that to the invoice's final status."""

    def __init__(self, session, invoice_id: int, workflow_run_id: int):
        self.session = session
        self.invoice_id = invoice_id
        self.workflow_run_id = workflow_run_id
        self.failed = False
        self.needs_validation = False
        self.failure_reason: str | None = None
        self.step_order = 0

    def run_step(self, tool_name: str, fn):
        started_at_monotonic = time.monotonic()
        started_at = _utcnow()
        is_verify_step = tool_name.startswith("Verify")
        try:
            result = fn()
            status_label = _tool_status_label(result.status)
            output_json = dict(result.data or {})
        except StepFailure as exc:
            result = None
            status_label = "failed"
            output_json = {}
            self.failed = True
            reason = exc.reason
            if self.failure_reason is None:
                self.failure_reason = f"{tool_name}: {reason}"
        except Exception as exc:  # noqa: BLE001 - must not crash the job, same reasoning as runner.py
            result = None
            status_label = "failed"
            output_json = {}
            self.failed = True
            reason = f"unexpected error: {exc}"
            if self.failure_reason is None:
                self.failure_reason = f"{tool_name}: {reason}"
        else:
            reason = result.reason
            if is_verify_step and result.status != ToolStatus.SUCCESS:
                self.needs_validation = True

        if result is None and is_verify_step:
            # A Verify* step erroring outright is still "couldn't
            # confirm the write," not "the pipeline is broken."
            self.needs_validation = True

        duration_ms = int((time.monotonic() - started_at_monotonic) * 1000)
        self.session.add(
            ToolRun(
                invoice_id=self.invoice_id,
                workflow_run_id=self.workflow_run_id,
                tool_name=tool_name,
                input_json={},
                output_json={**output_json, **({"reason": reason} if reason else {})},
                status=status_label,
                duration_ms=duration_ms,
                started_at=started_at,
                finished_at=_utcnow(),
            )
        )
        self.session.commit()
        self.step_order += 1
        return result


def _utcnow():
    return datetime.now(timezone.utc)


def process_workflow_run(invoice_id: int) -> None:
    with _SESSION_FACTORY() as session:
        invoice = session.get(Invoice, invoice_id)
        if invoice is None:
            return

        lock_token = try_acquire_session_lock()
        if lock_token is None:
            invoice.status = s.FAILED
            invoice.payload_json = {
                **(invoice.payload_json or {}),
                "workflow_error": "could not start — another DealerCenter session was active",
            }
            session.commit()
            notify_dealercenter_result(
                invoice, status="failed", message="could not start — another DealerCenter session was active"
            )
            return

        try:
            workflow = _get_or_create_workflow(session)
            workflow_run = WorkflowRun(
                invoice_id=invoice.id,
                workflow_id=workflow.id,
                status="running",
                started_at=_utcnow(),
            )
            session.add(workflow_run)
            invoice.status = s.RUNNING
            session.commit()

            tracker = _RunTracker(session, invoice.id, workflow_run.id)
            cost_add_result = None
            cost_verified = False

            try:
                with create_playwright_driver(headless=True, storage_state=_load_saved_state()) as driver:
                    login_result = tracker.run_step(
                        "LoginTool",
                        lambda: LoginTool().run(
                            driver,
                            username=os.environ["DEALER_CENTER_USERNAME"],
                            password=os.environ["DEALER_CENTER_PASSWORD"],
                            mfa_code_provider=_no_human_mfa_provider,
                        ),
                    )

                    if login_result is not None:
                        search_result = tracker.run_step(
                            "SearchInvoiceTool",
                            lambda: SearchInvoiceTool().run(
                                driver, vin=invoice.vin, stock_number=invoice.stock_number
                            ),
                        )

                        if search_result is not None:
                            category = (invoice.payload_json or {}).get("category", "Repair")
                            for attempt in range(1, MAX_SAVE_ATTEMPTS + 1):
                                cost_add_result = tracker.run_step(
                                    f"CostAddTool (attempt {attempt})",
                                    lambda: CostAddTool().run(
                                        driver,
                                        category=category,
                                        cost=invoice.grand_total,
                                        invoice_date=invoice.invoice_date,
                                        vendor_name=invoice.vendor_name,
                                        memo=invoice.invoice_number,
                                    ),
                                )
                                if cost_add_result is None or cost_add_result.status.name != "SUCCESS":
                                    break

                                verify_cost_result = tracker.run_step(
                                    f"VerifyCostAddTool (attempt {attempt})",
                                    lambda: VerifyCostAddTool().run(
                                        driver, memo=invoice.invoice_number, expected_cost=invoice.grand_total
                                    ),
                                )
                                cost_verified = (
                                    verify_cost_result is not None and verify_cost_result.status.name == "SUCCESS"
                                )
                                if cost_verified:
                                    break

                            if invoice.local_pdf_path:
                                upload_result = tracker.run_step(
                                    "FileUploadTool",
                                    lambda: FileUploadTool().run(
                                        driver,
                                        local_pdf_path=invoice.local_pdf_path,
                                        invoice_number=invoice.invoice_number,
                                    ),
                                )
                                if upload_result is not None and upload_result.status == ToolStatus.SUCCESS:
                                    uploaded_as = upload_result.data["uploaded_as"]
                                    tracker.run_step(
                                        "VerifyFileUploadTool",
                                        lambda: VerifyFileUploadTool().run(driver, file_name=uploaded_as),
                                    )
                            else:
                                tracker.needs_validation = True
            except Exception as exc:
                tracker.failed = True
                if tracker.failure_reason is None:
                    tracker.failure_reason = f"Pipeline: unexpected error: {exc}"
                session.add(
                    ToolRun(
                        invoice_id=invoice.id,
                        workflow_run_id=workflow_run.id,
                        tool_name="Pipeline",
                        input_json={},
                        output_json={"reason": f"unexpected error: {exc}"},
                        status="failed",
                        duration_ms=0,
                        started_at=_utcnow(),
                        finished_at=_utcnow(),
                    )
                )
                session.commit()

            if tracker.failed:
                invoice.status = s.FAILED
                workflow_run.status = "failed"
            elif tracker.needs_validation:
                invoice.status = s.PROCESSED_BUT_NEED_TO_VALIDATE
                workflow_run.status = "success"
            else:
                invoice.status = s.PROCESSED
                workflow_run.status = "success"
            workflow_run.finished_at = _utcnow()
            session.commit()

            dealercenter_status, dealercenter_message = _dealercenter_result(
                invoice, tracker, cost_add_result, cost_verified
            )
            notify_dealercenter_result(invoice, status=dealercenter_status, message=dealercenter_message)
        finally:
            release_session_lock(lock_token)
