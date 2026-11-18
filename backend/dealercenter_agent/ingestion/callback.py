"""Outbound notification to an invoice's callback_url, in the exact
shape Aviara Connect's DealerCenter tab expects:

    POST <callback_url>
    Content-Type: application/json
    {"status": "created"|"exists"|"failed", "reference"?: str, "message"?: str}

On their side, our webhook's 2xx response already marks the invoice
"Sent" (queued / with the agent); THIS callback is what flips it to its
final result (Created / Already in DealerCenter / Failed) — or they
mark it Timed out themselves if nothing arrives within 30h. So this
only fires on a genuinely terminal outcome, never for intermediate
internal states (queued, running, ingesting) — see workflow/jobs.py and
ingestion/jobs.py for where it's called automatically, and
web/app.py's /invoices/{id}/sync-callback for the manual "Sync Aviara
Connect" button.

The URL is posted to exactly as given — keep the trailing slash, no
auth header: the signed token in the URL already identifies the
invoice."""

from dataclasses import dataclass

import httpx
from sqlalchemy import select

from ..db.models import Invoice, ToolRun, WorkflowRun
from . import status as s

_TIMEOUT_SECONDS = 5.0
_VALID_STATUSES = {"created", "exists", "failed"}
_MAX_REFERENCE_LEN = 200
_MAX_MESSAGE_LEN = 1000


def _build_payload(status: str, message: str | None, reference: str | None) -> dict:
    if status not in _VALID_STATUSES:
        raise ValueError(f"status must be one of {sorted(_VALID_STATUSES)}, got {status!r}")
    payload: dict = {"status": status}
    if reference:
        payload["reference"] = reference[:_MAX_REFERENCE_LEN]
    if message:
        payload["message"] = message[:_MAX_MESSAGE_LEN]
    return payload


def notify_dealercenter_result(
    invoice: Invoice,
    status: str,
    message: str | None = None,
    reference: str | None = None,
) -> None:
    """Fire-and-forget: a slow or dead receiver must never block or
    fail the pipeline step that triggered the result, so failures here
    are swallowed, not raised — same reasoning as the SAS-expiry
    handling in ingestion/jobs.py. Used by the automated pipeline
    (workflow/jobs.py, ingestion/jobs.py)."""
    payload = _build_payload(status, message, reference)
    if not invoice.callback_url:
        return
    try:
        httpx.post(invoice.callback_url, json=payload, timeout=_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001 - best-effort notification, never block the caller
        pass


@dataclass
class SyncResult:
    ok: bool
    status: str
    message: str | None
    error: str | None = None


def sync_dealercenter_result(
    invoice: Invoice,
    status: str,
    message: str | None = None,
    reference: str | None = None,
) -> SyncResult:
    """Same POST, same payload shape — but for the manual "Sync Aviara
    Connect" button, where a human deliberately asked for this to
    happen and deserves to know whether it actually reached them,
    instead of this being silently swallowed like the automated path."""
    payload = _build_payload(status, message, reference)
    if not invoice.callback_url:
        return SyncResult(ok=False, status=status, message=message, error="invoice has no callback_url")
    try:
        resp = httpx.post(invoice.callback_url, json=payload, timeout=_TIMEOUT_SECONDS)
        resp.raise_for_status()
        return SyncResult(ok=True, status=status, message=message)
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller, not swallowed
        return SyncResult(ok=False, status=status, message=message, error=str(exc))


# Ingestion-phase failures have no ToolRun history to inspect at all
# (the workflow never ran) — these stay message-only, built straight
# from invoice fields.
_INGESTION_FAILURE_BUILDERS = {
    s.FAILED_DATA_STORAGE: lambda invoice: "internal error while saving the invoice record — please resend",
    s.FAILED_FILE_STORAGE: lambda invoice: (invoice.payload_json or {}).get("ingestion_error")
    or "could not download the invoice file",
}

# invoice.status values that have a real workflow-run outcome worth
# reconstructing from ToolRun history (see _result_from_tool_runs).
_WORKFLOW_TERMINAL_STATUSES = {s.PROCESSED, s.PROCESSED_BUT_NEED_TO_VALIDATE, s.FAILED}


def _latest_tool_run(session, workflow_run_id: int, tool_name_prefix: str) -> ToolRun | None:
    """Most recent attempt of a tool within one workflow run — e.g. the
    second "CostAddTool (attempt 2)" row when the retry loop ran twice."""
    return session.scalars(
        select(ToolRun)
        .where(ToolRun.workflow_run_id == workflow_run_id, ToolRun.tool_name.like(f"{tool_name_prefix}%"))
        .order_by(ToolRun.id.desc())
    ).first()


def _result_from_tool_runs(session, invoice: Invoice) -> tuple[str, str] | None:
    """Reconstructs the real created/exists/failed outcome from the
    invoice's most recent WorkflowRun's actual ToolRun rows — the exact
    same decision workflow/jobs.py's _dealercenter_result makes live,
    just rebuilt from persisted data instead of the in-memory pipeline
    result. Confirmed required (2026-11-18): invoice.status alone can't
    tell "exists" from "created" (both just collapse to PROCESSED), so
    guessing "created" by default sent a false result to Aviara Connect
    for an invoice CostAddTool had actually reported EXISTS for."""
    latest_run = session.scalars(
        select(WorkflowRun).where(WorkflowRun.invoice_id == invoice.id).order_by(WorkflowRun.id.desc())
    ).first()
    if latest_run is None:
        return None

    cost_add_run = _latest_tool_run(session, latest_run.id, "CostAddTool")
    if cost_add_run is None:
        return None

    if cost_add_run.status == "exists":
        return "exists", "DealerCenter already had this entry"

    if cost_add_run.status == "success":
        verify_run = _latest_tool_run(session, latest_run.id, "VerifyCostAddTool")
        verified = verify_run is not None and verify_run.status == "success"
        if verified:
            target = invoice.stock_number or invoice.vin or invoice.invoice_number
            return "created", f"Cost added to stock {target}"
        return (
            "failed",
            "could not confirm the cost was saved in DealerCenter after multiple attempts — please check manually",
        )

    # cost_add_run.status == "failed" (or any other unexpected value)
    reason = (cost_add_run.output_json or {}).get("reason")
    return "failed", reason or "could not add the cost in DealerCenter"


def dealercenter_result_for_invoice(session, invoice: Invoice) -> tuple[str, str] | None:
    """Returns (status, message) for invoice.status's current terminal
    outcome, or None if invoice.status isn't terminal yet (RECEIVED,
    INGESTING, READY_FOR_PROCESSING, QUEUED, RUNNING) — there's nothing
    meaningful to sync yet at those points."""
    if invoice.status in _WORKFLOW_TERMINAL_STATUSES:
        result = _result_from_tool_runs(session, invoice)
        if result is not None:
            return result
        # No ToolRun history found (shouldn't happen for a real
        # workflow-produced status, but don't crash the sync button
        # over it) — fall back to the same conservative defaults as
        # before rather than returning nothing.
        if invoice.status == s.FAILED:
            message = (invoice.payload_json or {}).get("workflow_error") or "the workflow failed for an unknown reason"
            return "failed", message
        return (
            "failed",
            "could not confirm the cost was saved in DealerCenter after multiple attempts — please check manually",
        )

    builder = _INGESTION_FAILURE_BUILDERS.get(invoice.status)
    if builder is None:
        return None
    return "failed", builder(invoice)
