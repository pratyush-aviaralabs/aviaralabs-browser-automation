"""Runs the Login -> Search -> FileUpload -> VerifyFileUpload pipeline in
a background thread, emitting structured events (tool log lines + live
CDP screencast frames) that app.py forwards to a browser tab over
WebSocket. Headless, by design — the point of streaming frames to the UI
is that nothing needs a visible window on the server anymore.

Every session — pipeline run or ad-hoc Inspector — ends the same way:
it drops into a live click-to-interact-and-inspect loop on whatever page
it's sitting on, and stays open until the browser tab disconnects. This
is deliberate: closing the browser the instant a tool fails (the first
version of this did) throws away the exact page a human needs to look at
to find the real selector. Now the failure state IS the inspectable
state — no separate Inspector session needed to see what went wrong."""

import json
import os
import queue
import threading
import time
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

from ..automation.playwright_driver import create_playwright_driver
from ..tools.base import StepFailure, ToolStatus
from ..tools.file_upload_tool import FileUploadTool
from ..tools.login_tool import LoginTool
from ..tools.search_invoice_tool import SearchInvoiceTool
from ..tools.verify_file_upload_tool import VerifyFileUploadTool

PROJECT_ROOT = Path(__file__).resolve().parents[3]
STATE_FILE = PROJECT_ROOT / ".auth_state.json"

# uvicorn starts as a fresh process with no .env loaded — unlike the
# manual_*.py scripts, which each called this themselves, nothing else
# in the web app's import chain does this, so it has to happen here.
load_dotenv(PROJECT_ROOT / ".env")

EventSink = Callable[[dict], None]


class MfaWaiter:
    """Bridges the blocking pipeline thread and the async WebSocket: the
    pipeline thread calls this (as LoginTool's mfa_code_provider) and
    blocks on a plain threading.Event; app.py's WS handler sets the code
    and fires the event when the browser-side user submits it."""

    def __init__(self, emit: EventSink):
        self._emit = emit
        self._event = threading.Event()
        self._code: str | None = None

    def submit_code(self, code: str) -> None:
        self._code = code
        self._event.set()

    def __call__(self) -> str:
        self._emit({"type": "mfa_required"})
        self._event.wait()
        self._event.clear()
        code = self._code
        self._code = None
        return code or ""


def _load_saved_state() -> dict | None:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return None


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state))


def _start_screencast(page, emit: EventSink):
    """CDP Page.startScreencast streams JPEG frames of the live page —
    this is the actual mechanism behind the right-hand panel; there is no
    way to literally embed a native Chromium window in an <iframe>, so
    this is the production-realistic equivalent: a live video made of
    base64 JPEG frames pushed over the WebSocket."""
    cdp = page.context.new_cdp_session(page)

    def on_frame(params: dict) -> None:
        emit({"type": "frame", "data": params["data"]})
        try:
            cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
        except Exception:
            pass  # session may already be closed if the run just finished

    cdp.on("Page.screencastFrame", on_frame)
    cdp.send(
        "Page.startScreencast",
        {"format": "jpeg", "quality": 60, "maxWidth": 1280, "maxHeight": 800},
    )
    cdp.send("DOM.enable", {})
    return cdp


def _log(emit: EventSink, tool: str, status: str, data: dict | None = None, reason: str | None = None) -> None:
    emit(
        {
            "type": "log",
            "tool": tool,
            "status": status,
            "data": data or {},
            "reason": reason,
            "ts": time.time(),
        }
    )


def _login(driver, emit: EventSink, mfa_waiter: MfaWaiter) -> bool:
    try:
        login_result = LoginTool().run(
            driver,
            username=os.environ["DEALER_CENTER_USERNAME"],
            password=os.environ["DEALER_CENTER_PASSWORD"],
            mfa_code_provider=mfa_waiter,
        )
        _log(emit, "LoginTool", login_result.status, login_result.data)
    except StepFailure as exc:
        _log(emit, "LoginTool", "failed", reason=exc.reason)
        return False
    _save_state(driver.save_session())
    return True


def _interactive_loop(
    cdp, emit: EventSink, commands: "queue.Queue[dict]", stop_event: threading.Event
) -> None:
    """Drains click/type commands until the browser tab disconnects.
    Every click both actually clicks (via CDP Input.dispatchMouseEvent —
    real interaction, not a preview) AND reports back the clicked
    element's outerHTML, replacing the manual 'paste this into your
    browser console' round-trip we were doing by hand to find each
    tool's real selectors."""
    emit({"type": "interactive_ready"})
    while not stop_event.is_set():
        try:
            cmd = commands.get(timeout=0.2)
        except queue.Empty:
            continue

        try:
            if cmd["type"] == "click":
                x, y = cmd["x"], cmd["y"]
                cdp.send(
                    "Input.dispatchMouseEvent",
                    {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1},
                )
                cdp.send(
                    "Input.dispatchMouseEvent",
                    {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1},
                )
                node = cdp.send("DOM.getNodeForLocation", {"x": x, "y": y})
                html = cdp.send("DOM.getOuterHTML", {"backendNodeId": node["backendNodeId"]})
                emit({"type": "inspect", "outer_html": html["outerHTML"]})
            elif cmd["type"] == "type":
                cdp.send("Input.insertText", {"text": cmd["text"]})
        except Exception as exc:
            emit({"type": "inspect_error", "message": str(exc)})


def run_pipeline(
    *,
    vin: str | None,
    stock_number: str | None,
    vendor_name: str,
    invoice_number: str,
    local_pdf_path: str,
    emit: EventSink,
    mfa_waiter: MfaWaiter,
    commands: "queue.Queue[dict]",
    stop_event: threading.Event,
) -> None:
    with create_playwright_driver(headless=True, storage_state=_load_saved_state()) as driver:
        cdp = _start_screencast(driver.page, emit)

        if _login(driver, emit, mfa_waiter):
            try:
                search_result = SearchInvoiceTool().run(driver, vin=vin, stock_number=stock_number)
                _log(emit, "SearchInvoiceTool", search_result.status, search_result.data)

                if search_result.data.get("match_type") != "inventory":
                    _log(
                        emit,
                        "SearchInvoiceTool",
                        "stopped",
                        reason="Deal-path click-through isn't built yet",
                    )
                else:
                    file_name = f"{vendor_name.split()[0]} {invoice_number}"
                    try:
                        upload_result = FileUploadTool().run(
                            driver,
                            local_pdf_path=local_pdf_path,
                            file_name=file_name,
                            invoice_number=invoice_number,
                        )
                        _log(emit, "FileUploadTool", upload_result.status, upload_result.data)

                        if upload_result.status == ToolStatus.SUCCESS:
                            verify_result = VerifyFileUploadTool().run(driver, file_name=file_name)
                            _log(
                                emit,
                                "VerifyFileUploadTool",
                                verify_result.status,
                                reason=verify_result.reason,
                            )
                    except StepFailure as exc:
                        _log(emit, "FileUploadTool", "failed", reason=exc.reason)
            except StepFailure as exc:
                _log(emit, "SearchInvoiceTool", "failed", reason=exc.reason)

        emit({"type": "done"})
        # Stay open on whatever page the run ended on — success or
        # failure — so a human can click to interact/inspect right there,
        # instead of the browser closing and a separate Inspector session
        # having to start over unauthenticated from the login page.
        _interactive_loop(cdp, emit, commands, stop_event)


def run_inspector_session(
    emit: EventSink, commands: "queue.Queue[dict]", stop_event: threading.Event
) -> None:
    """Ad-hoc session with no automated steps — logs in (reusing the
    saved session if valid) and then goes straight into the same
    interactive loop run_pipeline ends with."""
    mfa_waiter = MfaWaiter(emit)
    with create_playwright_driver(headless=True, storage_state=_load_saved_state()) as driver:
        cdp = _start_screencast(driver.page, emit)
        _login(driver, emit, mfa_waiter)  # LoginTool already lands on the dashboard
        _interactive_loop(cdp, emit, commands, stop_event)
