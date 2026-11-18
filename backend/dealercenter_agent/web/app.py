"""Dev/ops UI: upload a VIN/stock + invoice PDF, watch the pipeline run
live — left panel is the tool-by-tool execution log, right panel is the
actual browser (via CDP screencast, not a literal embedded window).

Run with: uvicorn dealercenter_agent.web.app:app --reload
"""
import asyncio
import queue
import threading
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from ..config import DBConfig, StorageConfig
from ..db.engine import get_session_factory
from ..db.models import Invoice, ToolRun, Workflow, WorkflowRun
from ..ingestion import status as s
from ..ingestion.callback import dealercenter_result_for_invoice, sync_dealercenter_result
from ..queue.connection import get_invoice_ingestion_queue, get_workflow_run_queue
from ..workflow.jobs import process_workflow_run
from .runner import MfaWaiter, run_inspector_session, run_login_refresh_session, run_pipeline
from .schedulers import router as schedulers_router
from .webhooks import router as webhooks_router

app = FastAPI()
app.include_router(webhooks_router)
app.include_router(schedulers_router)

STATIC_DIR = Path(__file__).parent / "static"
_SESSION_FACTORY = get_session_factory(DBConfig())

# A real PDF for the UI's "Send test payload" / "Start load test" buttons
# to embed, so those exercise an actual successful ingestion rather than
# always hitting a dead URL. Dev-only — reused as the fixture tests
# already use, not a stand-in for real invoice data.
SAMPLE_INVOICE_PDF_PATH = Path(__file__).resolve().parents[2] / "tests" / "invoices" / "invoice-6ae29dc7ec582be7587b312c.pdf"


@app.get("/dev/sample-invoice.pdf")
async def dev_sample_invoice_pdf():
    return FileResponse(SAMPLE_INVOICE_PDF_PATH, media_type="application/pdf")


# Confirmed required (2026-11-18): every static page/asset here is dev
# tooling that gets edited constantly, and a plain FileResponse (even
# with its own ETag/Last-Modified) only revalidates if the browser
# bothers to ask — a normal navigation/reload often just reuses the
# cached copy and never does, so edits silently stop reaching the
# browser. no-cache forces that revalidation on every load; these are
# tiny files, the extra round-trip is negligible.
def _no_cache_file(path: Path, media_type: str) -> FileResponse:
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "no-cache"})


@app.get("/theme.css")
async def theme_css():
    return _no_cache_file(STATIC_DIR / "theme.css", "text/css")


@app.get("/loading.js")
async def loading_js():
    return _no_cache_file(STATIC_DIR / "loading.js", "application/javascript")


def _invoice_to_dict(invoice: Invoice) -> dict:
    return {
        "id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "vendor_name": invoice.vendor_name,
        "grand_total": float(invoice.grand_total),
        "invoice_date": invoice.invoice_date.isoformat(),
        "vin": invoice.vin,
        "stock_number": invoice.stock_number,
        "status": invoice.status,
        "category": (invoice.payload_json or {}).get("category", "Repair"),
        "local_pdf_path": invoice.local_pdf_path,
    }


class RunState:
    def __init__(self):
        self.clients: list[WebSocket] = []
        self.mfa_waiter: MfaWaiter | None = None
        # Present for every session now, pipeline or Inspector — every
        # run ends by staying open for live click-to-interact/inspect on
        # whatever page it stopped on (see runner.py's _interactive_loop).
        self.commands: "queue.Queue[dict]" = queue.Queue()
        self.stop_event: threading.Event = threading.Event()


RUNS: dict[str, RunState] = {}
_MAIN_LOOP: asyncio.AbstractEventLoop | None = None

# Only one browser session (pipeline run or Inspector) is allowed to be
# active at a time. Running two concurrently against the same account
# was the actual cause of a real bug: both wrote to the same
# .auth_state.json file at once, and the frontend's single screencast
# image/log panel can't tell two sessions' frames/logs apart anyway —
# they just interleaved, which is what looked like a broken login.
_ACTIVE_RUN_ID: str | None = None


def _active_session_running() -> bool:
    if _ACTIVE_RUN_ID is None:
        return False
    state = RUNS.get(_ACTIVE_RUN_ID)
    return state is not None and not state.stop_event.is_set()


@app.on_event("startup")
async def _capture_loop() -> None:
    global _MAIN_LOOP
    _MAIN_LOOP = asyncio.get_running_loop()


def _broadcast(run_id: str, payload: dict) -> None:
    """Called from the pipeline's background thread — hops onto the main
    asyncio loop to actually send, since WebSocket.send_json is a
    coroutine and this thread doesn't have its own event loop."""
    state = RUNS.get(run_id)
    if not state or _MAIN_LOOP is None:
        return
    for ws in list(state.clients):
        asyncio.run_coroutine_threadsafe(_safe_send(ws, payload), _MAIN_LOOP)


async def _safe_send(ws: WebSocket, payload: dict) -> None:
    try:
        await ws.send_json(payload)
    except Exception:
        pass  # client may have disconnected mid-run


@app.get("/")
async def index():
    return _no_cache_file(STATIC_DIR / "index.html", "text/html")


@app.get("/invoices.html")
async def invoices_page():
    return _no_cache_file(STATIC_DIR / "invoices.html", "text/html")


@app.get("/invoices")
async def list_invoices(page: int = 1, page_size: int = 10):
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    with _SESSION_FACTORY() as session:
        total = session.scalar(select(func.count()).select_from(Invoice))
        rows = session.scalars(
            select(Invoice)
            .order_by(Invoice.invoice_date.desc(), Invoice.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    return {
        "items": [_invoice_to_dict(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# Plain def, not async def — confirmed required (2026-11-18): these two
# get polled every ~1.5s by the UI's load-test panel at the same time
# hundreds of webhook POSTs are in flight. async def with a blocking
# SQLAlchemy call inside serializes everything on one event-loop
# thread; def lets FastAPI run it in its worker threadpool instead, so
# polling doesn't queue up behind the burst it's trying to observe.
#
# Registered BEFORE /invoices/{invoice_id} — confirmed required
# (2026-11-18): route matching is registration-order, and
# "status-by-prefix" syntactically matches {invoice_id}'s single-segment
# pattern; once matched there, FastAPI's int conversion fails and
# returns 422 without ever trying this route. Defining this one first
# means literal "status-by-prefix" matches here first instead.
@app.get("/invoices/status-by-prefix")
def invoices_status_by_prefix(prefix: str):
    with _SESSION_FACTORY() as session:
        matches = select(Invoice).where(Invoice.invoice_number.like(f"{prefix}%"))
        total = session.scalar(select(func.count()).select_from(matches.subquery()))
        ready = session.scalar(
            select(func.count()).select_from(matches.where(Invoice.status == s.READY_FOR_PROCESSING).subquery())
        )
        failed = session.scalar(
            select(func.count()).select_from(
                matches.where(Invoice.status.in_(s.INGESTION_FAILURE_STATUSES)).subquery()
            )
        )
    return {"total": total, "ready": ready, "failed": failed, "in_flight": total - ready - failed}


@app.get("/queue/status")
def queue_status():
    return {"depth": get_invoice_ingestion_queue().count}


@app.get("/workflow-queue/status")
def workflow_queue_status():
    return {"depth": get_workflow_run_queue().count}


# Can't select these for (re-)queueing — already in flight. Any other
# status (including FAILED and PROCESSED_BUT_NEED_TO_VALIDATE) is
# fair game to retry; the UI doesn't gatekeep what you choose to retry.
_NOT_QUEUEABLE_STATUSES = {s.QUEUED, s.RUNNING}


@app.post("/invoices/bulk-run")
def bulk_run_invoices(body: dict):
    invoice_ids = body.get("invoice_ids") or []
    if not invoice_ids:
        raise HTTPException(422, "invoice_ids must not be empty")

    queued_ids: list[int] = []
    skipped: list[dict] = []
    with _SESSION_FACTORY() as session:
        for invoice_id in invoice_ids:
            invoice = session.get(Invoice, invoice_id)
            if invoice is None:
                skipped.append({"id": invoice_id, "reason": "not found"})
                continue
            if invoice.status in _NOT_QUEUEABLE_STATUSES:
                skipped.append({"id": invoice_id, "reason": f"already {invoice.status}"})
                continue
            invoice.status = s.QUEUED
            queued_ids.append(invoice_id)
        session.commit()

    workflow_queue = get_workflow_run_queue()
    for invoice_id in queued_ids:
        workflow_queue.enqueue(process_workflow_run, invoice_id)

    return {"queued": queued_ids, "skipped": skipped}


@app.post("/invoices/{invoice_id}/sync-callback")
def sync_invoice_callback(invoice_id: int):
    """Manual "Sync Aviara Connect" button — resends the invoice's
    CURRENT status to its callback_url on demand, for when the
    automated callback never arrived or the sender wants to re-confirm
    without re-running the whole workflow."""
    with _SESSION_FACTORY() as session:
        invoice = session.get(Invoice, invoice_id)
        if invoice is None:
            raise HTTPException(404, "invoice not found")

        result = dealercenter_result_for_invoice(session, invoice)
        if result is None:
            raise HTTPException(
                422, f"invoice status {invoice.status!r} has no terminal result to sync yet"
            )
        status, message = result
        sync_result = sync_dealercenter_result(invoice, status=status, message=message)

    if not sync_result.ok:
        raise HTTPException(502, f"callback delivery failed: {sync_result.error}")
    return {"ok": True, "status": sync_result.status, "message": sync_result.message}


@app.get("/invoices/{invoice_id}")
async def get_invoice(invoice_id: int):
    with _SESSION_FACTORY() as session:
        invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(404, "invoice not found")
    return _invoice_to_dict(invoice)


@app.get("/workflow-runs.html")
async def workflow_runs_page():
    return _no_cache_file(STATIC_DIR / "workflow_runs.html", "text/html")


@app.get("/schedulers.html")
async def schedulers_page():
    return _no_cache_file(STATIC_DIR / "schedulers.html", "text/html")


@app.get("/workflow-runs")
async def list_workflow_runs(page: int = 1, page_size: int = 10):
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    with _SESSION_FACTORY() as session:
        total = session.scalar(select(func.count()).select_from(WorkflowRun))
        rows = session.scalars(
            select(WorkflowRun)
            .order_by(WorkflowRun.started_at.desc(), WorkflowRun.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        items = []
        for run in rows:
            invoice = session.get(Invoice, run.invoice_id)
            workflow = session.get(Workflow, run.workflow_id)
            items.append(
                {
                    "id": run.id,
                    "invoice_number": invoice.invoice_number if invoice else None,
                    "workflow_name": workflow.name if workflow else None,
                    "status": run.status,
                    "started_at": run.started_at.isoformat() if run.started_at else None,
                    "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                }
            )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.get("/workflow-runs/{run_id}")
async def get_workflow_run(run_id: int):
    with _SESSION_FACTORY() as session:
        run = session.get(WorkflowRun, run_id)
        if run is None:
            raise HTTPException(404, "workflow run not found")
        invoice = session.get(Invoice, run.invoice_id)
        workflow = session.get(Workflow, run.workflow_id)
        steps = session.scalars(
            select(ToolRun)
            .where(ToolRun.workflow_run_id == run.id)
            .order_by(ToolRun.started_at.asc(), ToolRun.id.asc())
        ).all()
        return {
            "id": run.id,
            "invoice_number": invoice.invoice_number if invoice else None,
            "workflow_name": workflow.name if workflow else None,
            "status": run.status,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "steps": [
                {
                    "tool_name": step.tool_name,
                    "status": step.status,
                    "duration_ms": step.duration_ms,
                    "started_at": step.started_at.isoformat() if step.started_at else None,
                    "finished_at": step.finished_at.isoformat() if step.finished_at else None,
                    "input_json": step.input_json,
                    "output_json": step.output_json,
                }
                for step in steps
            ],
        }


@app.post("/run")
async def start_run(
    vin: str = Form(""),
    stock_number: str = Form(""),
    vendor_name: str = Form(...),
    invoice_number: str = Form(...),
    category: str = Form(...),
    cost: str = Form(...),
    invoice_date: str = Form(...),
    pdf: UploadFile | None = File(None),
    # Set when the form was prefilled from an invoice that's already
    # been ingested (see invoices.html's prefill banner) — its file was
    # already downloaded by the ingestion job, so Run can use that
    # instead of making the user re-upload a copy of the same PDF.
    existing_pdf_path: str = Form(""),
):
    global _ACTIVE_RUN_ID
    if _active_session_running():
        raise HTTPException(409, "A session is already active — close it before starting another.")

    storage = StorageConfig()
    storage.storage_local_path.mkdir(parents=True, exist_ok=True)

    if pdf is not None and pdf.filename:
        run_id = uuid.uuid4().hex
        pdf_path = storage.storage_local_path / f"{run_id}.pdf"
        pdf_path.write_bytes(await pdf.read())
    elif existing_pdf_path:
        # existing_pdf_path is client-submitted form data — resolve and
        # confirm it's actually inside storage_local_path before trusting
        # it, so a tampered value can't be used to read an arbitrary file
        # off disk.
        resolved = (storage.storage_local_path / Path(existing_pdf_path).name).resolve()
        if resolved.parent != storage.storage_local_path.resolve() or not resolved.is_file():
            raise HTTPException(422, "existing_pdf_path does not refer to a file in storage")
        run_id = uuid.uuid4().hex
        pdf_path = resolved
    else:
        raise HTTPException(422, "either pdf or existing_pdf_path is required")

    _ACTIVE_RUN_ID = run_id

    state = RunState()
    RUNS[run_id] = state
    mfa_waiter = MfaWaiter(emit=lambda payload: _broadcast(run_id, payload))
    state.mfa_waiter = mfa_waiter

    thread = threading.Thread(
        target=run_pipeline,
        kwargs=dict(
            vin=vin or None,
            stock_number=stock_number or None,
            vendor_name=vendor_name,
            invoice_number=invoice_number,
            category=category,
            cost=Decimal(cost),
            invoice_date=datetime.strptime(invoice_date, "%Y-%m-%d").date(),
            local_pdf_path=str(pdf_path),
            emit=lambda payload: _broadcast(run_id, payload),
            mfa_waiter=mfa_waiter,
            commands=state.commands,
            stop_event=state.stop_event,
        ),
        daemon=True,
    )
    thread.start()
    return {"run_id": run_id}


@app.post("/inspect/start")
async def start_inspector():
    global _ACTIVE_RUN_ID
    if _active_session_running():
        raise HTTPException(409, "A session is already active — close it before starting another.")

    run_id = uuid.uuid4().hex
    _ACTIVE_RUN_ID = run_id
    state = RunState()
    RUNS[run_id] = state
    mfa_waiter = MfaWaiter(emit=lambda payload: _broadcast(run_id, payload))
    state.mfa_waiter = mfa_waiter

    thread = threading.Thread(
        target=run_inspector_session,
        args=(lambda payload: _broadcast(run_id, payload), mfa_waiter, state.commands, state.stop_event),
        daemon=True,
    )
    thread.start()
    return {"run_id": run_id}


@app.post("/login/refresh")
async def start_login_refresh():
    """Login-only, no interactive loop afterward — refreshes
    .auth_state.json with a human handling MFA live, so the headless
    workflow-run queue (which can't) has a valid session to load."""
    global _ACTIVE_RUN_ID
    if _active_session_running():
        raise HTTPException(409, "A session is already active — close it before starting another.")

    run_id = uuid.uuid4().hex
    _ACTIVE_RUN_ID = run_id
    state = RunState()
    RUNS[run_id] = state
    mfa_waiter = MfaWaiter(emit=lambda payload: _broadcast(run_id, payload))
    state.mfa_waiter = mfa_waiter

    thread = threading.Thread(
        target=run_login_refresh_session,
        args=(lambda payload: _broadcast(run_id, payload), mfa_waiter),
        daemon=True,
    )
    thread.start()
    return {"run_id": run_id}


@app.websocket("/ws/{run_id}")
async def ws_endpoint(websocket: WebSocket, run_id: str):
    await websocket.accept()
    state = RUNS.get(run_id)
    if state is None:
        await websocket.close()
        return
    state.clients.append(websocket)
    try:
        while True:
            msg = await websocket.receive_json()
            if msg.get("type") == "mfa_code" and state.mfa_waiter:
                state.mfa_waiter.submit_code(msg.get("code", ""))
            elif msg.get("type") in ("click", "type"):
                state.commands.put(msg)
    except WebSocketDisconnect:
        state.clients.remove(websocket)
        if not state.clients:
            # Last viewer left — stop driving a browser nobody can see
            # or click anymore (works for both a pipeline run and an
            # Inspector session, since both now end in the same
            # interactive loop).
            state.stop_event.set()
