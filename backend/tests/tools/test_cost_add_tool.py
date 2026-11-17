from datetime import date
from decimal import Decimal

import pytest

from dealercenter_agent.tools.base import StepFailure, ToolStatus
from dealercenter_agent.tools.cost_add_tool import (
    COST_ADDS_LINK_SELECTOR,
    MODAL_OPEN_SELECTOR,
    NEW_ROW_COST_SELECTOR,
    NEW_ROW_DATE_SELECTOR,
    NEW_ROW_CATEGORY_SELECTOR,
    NEW_ROW_MEMO_SELECTOR,
    SAVE_BUTTON_SELECTOR,
    CostAddTool,
)


def test_cost_add_fills_repair_row_and_saves(fake_driver_cost_add_vendor_found):
    driver = fake_driver_cost_add_vendor_found
    result = CostAddTool().run(
        driver,
        category="Repair",
        cost=Decimal("270.00"),
        invoice_date=date(2026, 11, 6),
        vendor_name="Jensen Automotive",
        memo="276289",
    )
    assert result.status == ToolStatus.SUCCESS
    assert result.data["category"] == "Repair"
    assert COST_ADDS_LINK_SELECTOR in driver.clicks
    assert (NEW_ROW_CATEGORY_SELECTOR, "Repair") in driver.select_calls
    assert driver.filled[NEW_ROW_COST_SELECTOR] == "270.00"
    assert driver.filled[NEW_ROW_DATE_SELECTOR] == "11/06/2026"
    assert driver.filled[NEW_ROW_MEMO_SELECTOR] == "276289"
    assert SAVE_BUTTON_SELECTOR in driver.clicks


def test_cost_add_fills_other_row(fake_driver_cost_add_vendor_found):
    result = CostAddTool().run(
        fake_driver_cost_add_vendor_found,
        category="Other",
        cost=Decimal("50.00"),
        invoice_date=date(2026, 1, 1),
        vendor_name="Jensen Automotive",
        memo="X",
    )
    assert result.data["category"] == "Other"


def test_cost_add_searches_vendor_by_first_word(fake_driver_cost_add_vendor_found):
    driver = fake_driver_cost_add_vendor_found
    CostAddTool().run(
        driver,
        category="Repair",
        cost=Decimal("10"),
        invoice_date=date(2026, 1, 1),
        vendor_name="Jensen Automotive Group",
        memo="X",
    )
    from dealercenter_agent.tools.cost_add_tool import VENDOR_SEARCH_INPUT_SELECTOR

    assert driver.filled[VENDOR_SEARCH_INPUT_SELECTOR] == "Jensen"


def test_cost_add_rejects_unknown_category(fake_driver_cost_add_vendor_found):
    with pytest.raises(StepFailure) as exc:
        CostAddTool().run(
            fake_driver_cost_add_vendor_found,
            category="Parts",
            cost=Decimal("10"),
            invoice_date=date(2026, 1, 1),
            vendor_name="Jensen",
            memo="X",
        )
    assert "unknown category" in exc.value.reason


def test_cost_add_skips_vendor_without_inventing_one_on_no_match(fake_driver_cost_add_no_vendor_match):
    # Vendor is best-effort, not required — Cost/Category/Memo still get
    # filled and saved; a missing vendor match is logged, never invented,
    # and must not abort the whole entry.
    driver = fake_driver_cost_add_no_vendor_match
    result = CostAddTool().run(
        driver,
        category="Repair",
        cost=Decimal("10"),
        invoice_date=date(2026, 1, 1),
        vendor_name="TotallyUnknownVendorXYZ",
        memo="X",
    )
    assert result.status == ToolStatus.SUCCESS
    assert "no vendor match" in result.data["warnings"][0]
    assert driver.filled[NEW_ROW_MEMO_SELECTOR] == "X"
    assert SAVE_BUTTON_SELECTOR in driver.clicks


def test_cost_add_skips_vendor_on_ambiguous_match(fake_driver_cost_add_ambiguous_vendor):
    driver = fake_driver_cost_add_ambiguous_vendor
    result = CostAddTool().run(
        driver,
        category="Repair",
        cost=Decimal("10"),
        invoice_date=date(2026, 1, 1),
        vendor_name="Jensen",
        memo="X",
    )
    assert result.status == ToolStatus.SUCCESS
    assert "ambiguous vendor" in result.data["warnings"][0]
    assert SAVE_BUTTON_SELECTOR in driver.clicks


def test_cost_add_fails_when_target_row_already_has_a_memo(fake_driver_cost_add_vendor_found):
    """The blank-row guard: if the row CostAddTool is about to fill
    already has a Memo value, something upstream (row count, a stale
    selector) didn't go as expected — must stop rather than risk writing
    into existing data."""
    driver = fake_driver_cost_add_vendor_found
    from dealercenter_agent.tools.cost_add_tool import NEW_ROW_MEMO_SELECTOR as ROW_MEMO

    driver.find_all_values_results[ROW_MEMO] = ["103355"]
    with pytest.raises(StepFailure) as exc:
        CostAddTool().run(
            driver,
            category="Repair",
            cost=Decimal("10"),
            invoice_date=date(2026, 1, 1),
            vendor_name="Jensen",
            memo="X",
        )
    assert "expected a blank new row" in exc.value.reason


def test_cost_add_returns_exists_without_any_writes_when_memo_already_present(
    fake_driver_cost_add_duplicate_memo,
):
    driver = fake_driver_cost_add_duplicate_memo
    result = CostAddTool().run(
        driver,
        category="Repair",
        cost=Decimal("270.00"),
        invoice_date=date(2026, 11, 6),
        vendor_name="Jensen Automotive",
        memo="276289",
    )
    assert result.status == ToolStatus.EXISTS
    assert result.data["memo"] == "276289"
    # Must not add a row, upload, or save — matches the existing skill's rule exactly.
    assert driver.select_calls == []
    assert NEW_ROW_COST_SELECTOR not in driver.filled
    assert SAVE_BUTTON_SELECTOR not in driver.clicks


def test_cost_add_skip_duplicate_check_forces_write_path(fake_driver_cost_add_duplicate_memo):
    from dealercenter_agent.tools.cost_add_tool import VENDOR_RESULT_ROW_SELECTOR

    driver = fake_driver_cost_add_duplicate_memo
    driver.find_all_text_results[VENDOR_RESULT_ROW_SELECTOR] = ["Jensen Tire & Auto"]
    result = CostAddTool().run(
        driver,
        category="Repair",
        cost=Decimal("270.00"),
        invoice_date=date(2026, 11, 6),
        vendor_name="Jensen",
        memo="276289",
        skip_duplicate_check=True,
    )
    assert result.status == ToolStatus.SUCCESS
    assert SAVE_BUTTON_SELECTOR in driver.clicks
    # Must click Add New first to get a definitely-fresh row, not trust
    # whatever the default last row already held.
    assert driver.clicks.count(MODAL_OPEN_SELECTOR) == 1


def test_cost_add_duplicate_check_ignores_spaces(fake_driver_cost_add_duplicate_memo):
    # Fixture's existing memo is "276 289" (with a space); target "276289"
    # (no space) must still be recognized as the same invoice number.
    result = CostAddTool().run(
        fake_driver_cost_add_duplicate_memo,
        category="Repair",
        cost=Decimal("1"),
        invoice_date=date(2026, 1, 1),
        vendor_name="Jensen",
        memo="276289",
    )
    assert result.status == ToolStatus.EXISTS


def test_cost_add_fails_when_modal_never_opens(fake_driver_cost_add_modal_never_opens):
    with pytest.raises(StepFailure) as exc:
        CostAddTool().run(
            fake_driver_cost_add_modal_never_opens,
            category="Repair",
            cost=Decimal("10"),
            invoice_date=date(2026, 1, 1),
            vendor_name="Jensen",
            memo="X",
        )
    assert "modal did not open" in exc.value.reason
