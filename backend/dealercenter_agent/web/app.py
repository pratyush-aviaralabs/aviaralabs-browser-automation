"""Dev/ops UI: upload a VIN/stock + invoice PDF, watch the pipeline run
live — left panel is the tool-by-tool execution log, right panel is the
actual browser (via CDP screencast, not a literal embedded window).

Run with: uvicorn dealercenter_agent.web.app:app --reload
"""
import asyncio
import queue
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from ..config import StorageConfig
from .runner import MfaWaiter, run_inspector_session, run_pipeline

app = FastAPI()

STATIC_DIR = Path(__file__).parent / "static"


class RunState:
    def __init__(self):
        self.clients: list[WebSocket] = []
        self.mfa_waiter: MfaWaiter | None = None
        # Only set for Inspector sessions — a pipeline run's WS handler
        # ignores click/type messages because these stay None for it.
        self.commands: "queue.Queue[dict] | None" = None
        self.stop_event: threading.Event | None = None


RUNS: dict[str, RunState] = {}
_MAIN_LOOP: asyncio.AbstractEventLoop | None = None


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
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/run")
async def start_run(
    vin: str = Form(""),
    stock_number: str = Form(""),
    vendor_name: str = Form(...),
    invoice_number: str = Form(...),
    pdf: UploadFile = File(...),
):
    run_id = uuid.uuid4().hex
    storage = StorageConfig()
    storage.storage_local_path.mkdir(parents=True, exist_ok=True)
    pdf_path = storage.storage_local_path / f"{run_id}.pdf"
    pdf_path.write_bytes(await pdf.read())

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
            local_pdf_path=str(pdf_path),
            emit=lambda payload: _broadcast(run_id, payload),
            mfa_waiter=mfa_waiter,
        ),
        daemon=True,
    )
    thread.start()
    return {"run_id": run_id}


@app.post("/inspect/start")
async def start_inspector():
    run_id = uuid.uuid4().hex
    state = RunState()
    state.commands = queue.Queue()
    state.stop_event = threading.Event()
    RUNS[run_id] = state

    thread = threading.Thread(
        target=run_inspector_session,
        args=(lambda payload: _broadcast(run_id, payload), state.commands, state.stop_event),
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
            elif msg.get("type") in ("click", "type") and state.commands is not None:
                state.commands.put(msg)
    except WebSocketDisconnect:
        state.clients.remove(websocket)
        if state.stop_event is not None and not state.clients:
            # Inspector session's only viewer left — stop driving a
            # browser nobody can see or click anymore.
            state.stop_event.set()
