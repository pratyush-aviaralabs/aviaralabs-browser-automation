from datetime import date
from decimal import Decimal

import pytest

from dealercenter_agent.tools.base import StepFailure, ToolStatus
from dealercenter_agent.tools.cost_add_tool import (
    COST_ADDS_LINK_SELECTOR,
    NEW_ROW_COST_SELECTOR,
    NEW_ROW_DATE_SELECTOR,
    NEW_ROW_DESCRIPTION_SELECTOR,
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
    assert (NEW_ROW_DESCRIPTION_SELECTOR, "Repair") in driver.select_calls
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


def test_cost_add_fails_without_inventing_a_vendor(fake_driver_cost_add_no_vendor_match):
    with pytest.raises(StepFailure) as exc:
        CostAddTool().run(
            fake_driver_cost_add_no_vendor_match,
            category="Repair",
            cost=Decimal("10"),
            invoice_date=date(2026, 1, 1),
            vendor_name="TotallyUnknownVendorXYZ",
            memo="X",
        )
    assert "no vendor match" in exc.value.reason


def test_cost_add_fails_on_ambiguous_vendor(fake_driver_cost_add_ambiguous_vendor):
    with pytest.raises(StepFailure) as exc:
        CostAddTool().run(
            fake_driver_cost_add_ambiguous_vendor,
            category="Repair",
            cost=Decimal("10"),
            invoice_date=date(2026, 1, 1),
            vendor_name="Jensen",
            memo="X",
        )
    assert "ambiguous vendor" in exc.value.reason


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
