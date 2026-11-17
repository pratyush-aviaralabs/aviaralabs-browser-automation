from decimal import Decimal

import pytest

from dealercenter_agent.tools.base import StepFailure, ToolStatus
from dealercenter_agent.tools.cost_add_tool import EXISTING_MEMO_SELECTOR, MODAL_OPEN_SELECTOR
from dealercenter_agent.tools.verify_cost_add_tool import ALL_COST_SELECTOR, VerifyCostAddTool

from ..fakes import FakeBrowserDriver


def _driver(memos: list[str], costs: list[str]) -> FakeBrowserDriver:
    driver = FakeBrowserDriver()
    driver.on_wait_visible = lambda sel, t: True
    driver.find_all_values_results[EXISTING_MEMO_SELECTOR] = memos
    driver.find_all_values_results[ALL_COST_SELECTOR] = costs
    return driver


def test_verify_succeeds_when_memo_and_cost_both_match():
    driver = _driver(memos=["00294", "110011300"], costs=["$38.60", "$119.12"])
    result = VerifyCostAddTool().run(driver, memo="110011300", expected_cost=Decimal("119.12"))
    assert result.status == ToolStatus.SUCCESS
    # Reproduces the exact live bug: DealerCenter's own Save button
    # never closes the dialog, so it's already open when this runs
    # right after CostAddTool's Save click — clicking the underlying
    # page's Cost Adds link in that state hangs 30s on its own backdrop.
    from dealercenter_agent.tools.cost_add_tool import COST_ADDS_LINK_SELECTOR

    assert COST_ADDS_LINK_SELECTOR not in driver.clicks


def test_verify_ignores_spaces_in_memo_like_cost_add_tool_does():
    driver = _driver(memos=["276 289"], costs=["$270.00"])
    result = VerifyCostAddTool().run(driver, memo="276289", expected_cost=Decimal("270.00"))
    assert result.status == ToolStatus.SUCCESS


def test_verify_fails_when_memo_not_found(monkeypatch):
    import dealercenter_agent.tools.verify_cost_add_tool as verify_cost_add_tool_module

    monkeypatch.setattr(verify_cost_add_tool_module, "VERIFY_RETRY_DELAY_SECONDS", 0)

    driver = _driver(memos=["00294"], costs=["$38.60"])
    result = VerifyCostAddTool().run(driver, memo="110011300", expected_cost=Decimal("119.12"))
    assert result.status == ToolStatus.FAILED
    assert "not found" in result.reason


def test_verify_retries_the_reopen_cycle_until_the_row_shows_up(monkeypatch):
    """Reproduces the exact live bug: reopening the dialog immediately
    after Save can still show pre-save grid data — manually waiting a
    moment before reopening found the row fine, an instant reopen
    didn't. Models that as the memo only appearing on the 3rd view."""
    import dealercenter_agent.tools.verify_cost_add_tool as verify_cost_add_tool_module
    from dealercenter_agent.tools.cost_add_tool import COST_ADDS_LINK_SELECTOR, EXISTING_MEMO_SELECTOR
    from dealercenter_agent.tools.verify_cost_add_tool import ALL_COST_SELECTOR, COST_ADDS_CLOSE_SELECTOR

    monkeypatch.setattr(verify_cost_add_tool_module, "VERIFY_RETRY_DELAY_SECONDS", 0)

    driver = FakeBrowserDriver()
    # Starts already open — same as right after CostAddTool's own Save
    # click, which (confirmed live 2026-11-18) never closes the dialog.
    state = {"dialog_open": True, "views": 0}

    def on_wait_visible(selector: str, _timeout_ms: int) -> bool:
        return state["dialog_open"] if selector == MODAL_OPEN_SELECTOR else True

    def on_click(selector: str) -> None:
        if selector == COST_ADDS_LINK_SELECTOR:
            state["dialog_open"] = True
        elif selector == COST_ADDS_CLOSE_SELECTOR:
            state["dialog_open"] = False

    def on_find_all_values(selector: str) -> list[str]:
        if selector == EXISTING_MEMO_SELECTOR:
            state["views"] += 1
            return ["110011300"] if state["views"] >= 3 else ["00294"]
        if selector == ALL_COST_SELECTOR:
            return ["$119.12"] if state["views"] >= 3 else ["$38.60"]
        return []

    driver.on_wait_visible = on_wait_visible
    driver.on_click = on_click
    driver.on_find_all_values = on_find_all_values

    result = VerifyCostAddTool().run(driver, memo="110011300", expected_cost=Decimal("119.12"))
    assert result.status == ToolStatus.SUCCESS
    # Closed before the 2nd and 3rd view, plus once more unconditionally at the end.
    assert driver.clicks.count(COST_ADDS_CLOSE_SELECTOR) == 3
    # 1st view: already open, no link click needed. 2nd and 3rd: reopened via the link.
    assert driver.clicks.count(COST_ADDS_LINK_SELECTOR) == 2


def test_verify_fails_when_cost_does_not_match():
    """Reproduces the exact live bug: a save can report SUCCESS and
    even show the right fields in the browser, but silently fail to
    persist one of them — Memo specifically came back blank after a
    refresh on 2026-11-18 even though Category/Cost/Vendor/Date all
    survived. This is the independent check that catches that."""
    driver = _driver(memos=["110011300"], costs=["$0.00"])
    result = VerifyCostAddTool().run(driver, memo="110011300", expected_cost=Decimal("119.12"))
    assert result.status == ToolStatus.FAILED
    assert "Cost is" in result.reason


def test_verify_fails_on_ambiguous_memo_match():
    driver = _driver(memos=["110011300", "110011300"], costs=["$119.12", "$119.12"])
    result = VerifyCostAddTool().run(driver, memo="110011300", expected_cost=Decimal("119.12"))
    assert result.status == ToolStatus.FAILED
    assert "ambiguous" in result.reason


def test_verify_fails_when_modal_never_opens():
    driver = FakeBrowserDriver()
    driver.on_wait_visible = lambda sel, t: False
    with pytest.raises(StepFailure) as exc:
        VerifyCostAddTool().run(driver, memo="110011300", expected_cost=Decimal("119.12"))
    assert "modal did not open" in exc.value.reason
