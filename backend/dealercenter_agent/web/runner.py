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

import os
import queue
import threading
import time
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

from ..automation.playwright_driver import create_playwright_driver
from ..automation.session_state import load_saved_state, save_state
from ..queue.session_lock import release_session_lock, try_acquire_session_lock
from ..tools.base import StepFailure, ToolStatus
from ..tools.cost_add_tool import CostAddTool
from ..tools.file_upload_tool import FileUploadTool
from ..tools.login_tool import LoginTool
from ..tools.search_invoice_tool import SearchInvoiceTool
from ..tools.verify_cost_add_tool import VerifyCostAddTool
from ..tools.verify_file_upload_tool import VerifyFileUploadTool

PROJECT_ROOT = Path(__file__).resolve().parents[3]
STATE_FILE = PROJECT_ROOT / ".auth_state.json"

# Confirmed live (2026-11-18): a reported SUCCESS save can still
# silently fail to persist server-side — retry the whole CostAddTool
# write once more if verification fails, same as the manual scripts.
MAX_SAVE_ATTEMPTS = 2

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
    return load_saved_state(STATE_FILE)


def _save_state(state: dict) -> None:
    save_state(STATE_FILE, state)


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


def _run_step(emit: EventSink, tool_name: str, fn):
    """Runs one tool call, catching StepFailure AND any other exception —
    a raw Playwright TimeoutError from an unverified placeholder selector
    is exactly as likely as a StepFailure at this stage, and letting
    either one escape uncaught crashes the whole thread before the
    interactive loop gets a chance to run, which silently throws away
    the 'stay open for inspection' guarantee the rest of this module
    exists to provide. Returns the ToolResult, or None on any failure."""
    try:
        result = fn()
        _log(emit, tool_name, result.status, result.data, result.reason)
        return result
    except StepFailure as exc:
        _log(emit, tool_name, "failed", reason=exc.reason)
        return None
    except Exception as exc:
        _log(emit, tool_name, "failed", reason=f"unexpected error: {exc}")
        return None


def _login(driver, emit: EventSink, mfa_waiter: MfaWaiter) -> bool:
    result = _run_step(
        emit,
        "LoginTool",
        lambda: LoginTool().run(
            driver,
            username=os.environ["DEALER_CENTER_USERNAME"],
            password=os.environ["DEALER_CENTER_PASSWORD"],
            mfa_code_provider=mfa_waiter,
        ),
    )
    if result is None:
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
    category: str,
    cost: Decimal,
    invoice_date: date,
    local_pdf_path: str,
    emit: EventSink,
    mfa_waiter: MfaWaiter,
    commands: "queue.Queue[dict]",
    stop_event: threading.Event,
) -> None:
    # Cross-process guard — confirmed required (2026-11-18): a future
    # headless workflow-run worker (separate process) opens real
    # DealerCenter sessions too, and the in-memory _ACTIVE_RUN_ID check
    # in app.py only protects against collisions within this one
    # process. This Redis-backed lock is the one both sides can see.
    lock_token = try_acquire_session_lock()
    if lock_token is None:
        _log(emit, "SessionLock", "failed", reason="another DealerCenter session is already active")
        emit({"type": "done"})
        return

    try:
        with create_playwright_driver(headless=True, storage_state=_load_saved_state()) as driver:
            try:
                cdp = _start_screencast(driver.page, emit)
            except Exception as exc:
                # No CDP session to hand to the interactive loop — this is
                # the one failure mode that genuinely can't stay open for
                # inspection, since there's no screencast to show.
                _log(emit, "ScreencastSetup", "failed", reason=str(exc))
                emit({"type": "done"})
                return

            try:
                if _login(driver, emit, mfa_waiter):
                    search_result = _run_step(
                        emit,
                        "SearchInvoiceTool",
                        lambda: SearchInvoiceTool().run(driver, vin=vin, stock_number=stock_number),
                    )

                    if search_result is not None:
                        # Both Inventory and Deal matches proceed the same
                        # way from here — confirmed live (2026-11-18) that
                        # a Deal match's "Edit Vehicle" surface is
                        # functionally identical to the Inventory page's
                        # own vehicle detail view, and SearchInvoiceTool
                        # already switched the driver into that iframe
                        # context for a Deal match, so CostAddTool/
                        # FileUploadTool work completely unchanged either way.
                        for attempt in range(1, MAX_SAVE_ATTEMPTS + 1):
                            cost_add_result = _run_step(
                                emit,
                                f"CostAddTool (attempt {attempt})",
                                lambda: CostAddTool().run(
                                    driver,
                                    category=category,
                                    cost=cost,
                                    invoice_date=invoice_date,
                                    vendor_name=vendor_name,
                                    memo=invoice_number,
                                ),
                            )
                            if cost_add_result is None or cost_add_result.status.name != "SUCCESS":
                                break  # FAILED or EXISTS — nothing new was saved, don't retry

                            # Don't trust CostAddTool's own SUCCESS —
                            # confirmed live (2026-11-18) that a reported
                            # SUCCESS save can still silently fail to
                            # persist server-side.
                            verify_cost_result = _run_step(
                                emit,
                                f"VerifyCostAddTool (attempt {attempt})",
                                lambda: VerifyCostAddTool().run(
                                    driver, memo=invoice_number, expected_cost=cost
                                ),
                            )
                            if verify_cost_result is not None and verify_cost_result.status.name == "SUCCESS":
                                break

                        upload_result = _run_step(
                            emit,
                            "FileUploadTool",
                            lambda: FileUploadTool().run(
                                driver,
                                local_pdf_path=local_pdf_path,
                                invoice_number=invoice_number,
                            ),
                        )

                        if upload_result is not None and upload_result.status == ToolStatus.SUCCESS:
                            uploaded_as = upload_result.data["uploaded_as"]
                            _run_step(
                                emit,
                                "VerifyFileUploadTool",
                                lambda: VerifyFileUploadTool().run(driver, file_name=uploaded_as),
                            )
            except Exception as exc:
                # Belt-and-suspenders: _run_step already catches every
                # per-tool failure, so reaching here means a bug in this
                # glue code itself, not a tool. Still must not skip the
                # interactive loop below.
                _log(emit, "Pipeline", "failed", reason=f"unexpected error: {exc}")

            emit({"type": "done"})
            # Stay open on whatever page the run ended on — success or
            # failure — so a human can click to interact/inspect right there,
            # instead of the browser closing and a separate Inspector session
            # having to start over unauthenticated from the login page.
            _interactive_loop(cdp, emit, commands, stop_event)
    finally:
        release_session_lock(lock_token)


def run_inspector_session(
    emit: EventSink,
    mfa_waiter: MfaWaiter,
    commands: "queue.Queue[dict]",
    stop_event: threading.Event,
) -> None:
    """Ad-hoc session with no automated steps — logs in (reusing the
    saved session if valid) and then goes straight into the same
    interactive loop run_pipeline ends with.

    mfa_waiter is now passed in rather than created here — confirmed a
    real bug (2026-11-18): app.py never wired this function's own
    internally-created waiter into RunState.mfa_waiter, so a submitted
    MFA code during an Inspector session had nowhere to go. Taking it
    as a parameter, same as run_pipeline, lets the caller register it
    before start."""
    lock_token = try_acquire_session_lock()
    if lock_token is None:
        _log(emit, "SessionLock", "failed", reason="another DealerCenter session is already active")
        emit({"type": "done"})
        return

    try:
        with create_playwright_driver(headless=True, storage_state=_load_saved_state()) as driver:
            cdp = _start_screencast(driver.page, emit)
            _login(driver, emit, mfa_waiter)  # LoginTool already lands on the dashboard
            _interactive_loop(cdp, emit, commands, stop_event)
    finally:
        release_session_lock(lock_token)


def run_login_refresh_session(emit: EventSink, mfa_waiter: MfaWaiter) -> None:
    """Login-only — no search/cost-add/upload, no interactive loop
    afterward. Purpose: refresh .auth_state.json (handling MFA live,
    with a human watching) so the headless workflow-run queue — which
    can never handle an MFA challenge, since nobody's there to answer
    it — has a valid session to load. Closes the browser the moment
    login finishes, success or failure."""
    lock_token = try_acquire_session_lock()
    if lock_token is None:
        _log(emit, "SessionLock", "failed", reason="another DealerCenter session is already active")
        emit({"type": "done"})
        return

    try:
        with create_playwright_driver(headless=True, storage_state=_load_saved_state()) as driver:
            _start_screencast(driver.page, emit)  # so MFA entry is visible live
            _login(driver, emit, mfa_waiter)
            emit({"type": "done"})
    finally:
        release_session_lock(lock_token)
