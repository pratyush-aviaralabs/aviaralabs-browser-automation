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


def test_verify_ignores_spaces_in_memo_like_cost_add_tool_does():
    driver = _driver(memos=["276 289"], costs=["$270.00"])
    result = VerifyCostAddTool().run(driver, memo="276289", expected_cost=Decimal("270.00"))
    assert result.status == ToolStatus.SUCCESS


def test_verify_fails_when_memo_not_found():
    driver = _driver(memos=["00294"], costs=["$38.60"])
    result = VerifyCostAddTool().run(driver, memo="110011300", expected_cost=Decimal("119.12"))
    assert result.status == ToolStatus.FAILED
    assert "not found" in result.reason


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
