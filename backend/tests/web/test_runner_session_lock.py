"""Confirms run_pipeline/run_inspector_session/run_login_refresh_session
all refuse to open a browser when the cross-process session lock is
already held — the one thing standing between a queued workflow-run
worker and a manual Live Run/Login-refresh colliding on the same
DealerCenter account."""

from datetime import date
from decimal import Decimal

import pytest

from dealercenter_agent.web import runner


class _ShouldNotBeCalled(Exception):
    pass


@pytest.fixture
def lock_already_held(monkeypatch):
    monkeypatch.setattr(runner, "try_acquire_session_lock", lambda: None)
    monkeypatch.setattr(runner, "release_session_lock", lambda token: None)

    def fail_if_called(*args, **kwargs):
        raise _ShouldNotBeCalled("create_playwright_driver must not be called when the lock is held")

    monkeypatch.setattr(runner, "create_playwright_driver", fail_if_called)


def _collect_emit():
    events = []
    return events, events.append


def test_run_pipeline_refuses_to_start_when_lock_is_held(lock_already_held):
    events, emit = _collect_emit()
    runner.run_pipeline(
        vin="1FTFW1E81NFA67507",
        stock_number=None,
        vendor_name="Vendor",
        invoice_number="INV-1",
        category="Repair",
        cost=Decimal("100.00"),
        invoice_date=date(2026, 11, 18),
        local_pdf_path="/tmp/fake.pdf",
        emit=emit,
        mfa_waiter=lambda: "",
        commands=None,
        stop_event=None,
    )
    assert any(e.get("tool") == "SessionLock" and e.get("status") == "failed" for e in events)
    assert any(e.get("type") == "done" for e in events)


def test_run_inspector_session_refuses_to_start_when_lock_is_held(lock_already_held):
    events, emit = _collect_emit()
    runner.run_inspector_session(emit, mfa_waiter=lambda: "", commands=None, stop_event=None)
    assert any(e.get("tool") == "SessionLock" and e.get("status") == "failed" for e in events)
    assert any(e.get("type") == "done" for e in events)


def test_run_login_refresh_session_refuses_to_start_when_lock_is_held(lock_already_held):
    events, emit = _collect_emit()
    runner.run_login_refresh_session(emit, mfa_waiter=lambda: "")
    assert any(e.get("tool") == "SessionLock" and e.get("status") == "failed" for e in events)
    assert any(e.get("type") == "done" for e in events)


def test_run_pipeline_releases_lock_even_when_browser_setup_fails(monkeypatch):
    released = {"called_with": None}
    monkeypatch.setattr(runner, "try_acquire_session_lock", lambda: "token-123")
    monkeypatch.setattr(runner, "release_session_lock", lambda token: released.__setitem__("called_with", token))

    class _BoomDriver:
        def __enter__(self):
            raise RuntimeError("playwright failed to launch")

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(runner, "create_playwright_driver", lambda **kwargs: _BoomDriver())

    events, emit = _collect_emit()
    with pytest.raises(RuntimeError):
        runner.run_pipeline(
            vin=None,
            stock_number="12345",
            vendor_name="Vendor",
            invoice_number="INV-1",
            category="Repair",
            cost=Decimal("100.00"),
            invoice_date=date(2026, 11, 18),
            local_pdf_path="/tmp/fake.pdf",
            emit=emit,
            mfa_waiter=lambda: "",
            commands=None,
            stop_event=None,
        )
    assert released["called_with"] == "token-123"
