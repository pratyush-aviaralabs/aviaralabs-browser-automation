from datetime import date
from decimal import Decimal

import pytest

from dealercenter_agent.tools.base import StepFailure, ToolStatus
from dealercenter_agent.tools.cost_add_tool import (
    COST_ADDS_CLOSE_SELECTOR,
    COST_ADDS_LINK_SELECTOR,
    COST_ADDS_SAVE_BUTTON_SELECTOR,
    MODAL_OPEN_SELECTOR,
    NEW_ROW_COST_SELECTOR,
    NEW_ROW_DATE_SELECTOR,
    NEW_ROW_CATEGORY_SELECTOR,
    NEW_ROW_MEMO_SELECTOR,
    SAVE_CONFIRMATION_YES_SELECTOR,
    CostAddTool,
)


def test_cost_add_fills_repair_row_and_saves(fake_driver_cost_add_vendor_found):
    driver = fake_driver_cost_add_vendor_found
    result = CostAddTool().run(
        driver,
        category="Repair",
        cost=Decimal("270.00"),
        invoice_date=date(2026, 11, 6),
        vendor_name="Jensen Tire & Auto",
        memo="276289",
    )
    assert result.status == ToolStatus.SUCCESS
    assert result.data["category"] == "Repair"
    assert "warnings" not in result.data  # exact match found — no warning expected
    assert COST_ADDS_LINK_SELECTOR in driver.clicks
    assert (NEW_ROW_CATEGORY_SELECTOR, "Repair") in driver.select_calls
    assert driver.filled[NEW_ROW_COST_SELECTOR] == "270.00"
    assert driver.typed[NEW_ROW_DATE_SELECTOR] == "11062026"
    assert driver.typed[NEW_ROW_MEMO_SELECTOR] == "276289"
    assert COST_ADDS_SAVE_BUTTON_SELECTOR in driver.clicks
    assert COST_ADDS_CLOSE_SELECTOR in driver.clicks
    assert SAVE_CONFIRMATION_YES_SELECTOR in driver.clicks


def test_cost_add_clean_close_with_no_prompt_is_still_success(fake_driver_cost_add_vendor_found):
    """If the direct in-dialog Save button already took effect, closing
    the dialog afterward has nothing unsaved to prompt about — no
    "Save Confirmation" dialog appears, and that's fine, not a failure."""
    driver = fake_driver_cost_add_vendor_found

    def on_wait_visible(selector: str, timeout_ms: int) -> bool:
        return selector != SAVE_CONFIRMATION_YES_SELECTOR

    driver.on_wait_visible = on_wait_visible

    result = CostAddTool().run(
        driver,
        category="Repair",
        cost=Decimal("270.00"),
        invoice_date=date(2026, 11, 6),
        vendor_name="Jensen Tire & Auto",
        memo="276289",
    )
    assert result.status == ToolStatus.SUCCESS
    assert COST_ADDS_SAVE_BUTTON_SELECTOR in driver.clicks
    assert COST_ADDS_CLOSE_SELECTOR in driver.clicks
    assert SAVE_CONFIRMATION_YES_SELECTOR not in driver.clicks


def test_cost_add_auto_save_false_fills_everything_but_does_not_click_save(
    fake_driver_cost_add_vendor_found,
):
    driver = fake_driver_cost_add_vendor_found
    result = CostAddTool().run(
        driver,
        category="Repair",
        cost=Decimal("270.00"),
        invoice_date=date(2026, 11, 6),
        vendor_name="Jensen Tire & Auto",
        memo="276289",
        auto_save=False,
    )
    assert result.status == ToolStatus.SUCCESS
    assert result.data["auto_save"] is False
    assert (NEW_ROW_CATEGORY_SELECTOR, "Repair") in driver.select_calls
    assert driver.typed[NEW_ROW_MEMO_SELECTOR] == "276289"
    assert COST_ADDS_CLOSE_SELECTOR not in driver.clicks


def test_cost_add_fills_other_row(fake_driver_cost_add_vendor_found):
    result = CostAddTool().run(
        fake_driver_cost_add_vendor_found,
        category="Other",
        cost=Decimal("50.00"),
        invoice_date=date(2026, 1, 1),
        vendor_name="Jensen Tire & Auto",
        memo="X",
    )
    assert result.data["category"] == "Other"


def test_cost_add_falls_back_to_first_word_when_full_name_search_finds_nothing(
    fake_driver_cost_add_vendor_fallback_to_first_word,
):
    # Confirmed live (2026-11-18): the dialog does prefix matching, so a
    # multi-word vendor_name searched in full can return zero hits even
    # though the vendor exists — must retry with just the first word
    # and then pick the exact match out of THOSE candidates.
    driver = fake_driver_cost_add_vendor_fallback_to_first_word
    result = CostAddTool().run(
        driver,
        category="Repair",
        cost=Decimal("10"),
        invoice_date=date(2026, 1, 1),
        vendor_name="Jensen Automotive Group",
        memo="X",
    )
    from dealercenter_agent.tools.cost_add_tool import VENDOR_SEARCH_INPUT_SELECTOR

    assert result.status == ToolStatus.SUCCESS
    assert "warnings" not in result.data
    assert driver.typed[VENDOR_SEARCH_INPUT_SELECTOR] == "Jensen"


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
    assert "no result matched" in result.data["warnings"][0]
    assert driver.typed[NEW_ROW_MEMO_SELECTOR] == "X"
    assert COST_ADDS_CLOSE_SELECTOR in driver.clicks
    assert SAVE_CONFIRMATION_YES_SELECTOR in driver.clicks


def test_cost_add_skips_vendor_when_no_candidate_is_an_exact_match(fake_driver_cost_add_ambiguous_vendor):
    # Multiple candidates come back ("Jensen Tire", "Jensen Auto Body")
    # but NEITHER is an exact match for "Jensen" — must report that, not
    # silently guess one of them.
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
    assert "no result matched" in result.data["warnings"][0]
    assert "2 candidates" in result.data["warnings"][0]
    assert COST_ADDS_CLOSE_SELECTOR in driver.clicks
    assert SAVE_CONFIRMATION_YES_SELECTOR in driver.clicks


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
    assert COST_ADDS_CLOSE_SELECTOR not in driver.clicks


def test_cost_add_skip_duplicate_check_forces_write_path(fake_driver_cost_add_duplicate_memo):
    from dealercenter_agent.tools.cost_add_tool import ROW_SELECTOR, VENDOR_RESULT_ROW_SELECTOR

    driver = fake_driver_cost_add_duplicate_memo
    row_count = {"n": 5}
    vendor_calls = {"n": 0}

    def on_click(selector: str) -> None:
        if selector == MODAL_OPEN_SELECTOR:
            row_count["n"] += 1

    def on_find_all_text(selector: str) -> list[str]:
        if selector == ROW_SELECTOR:
            return ["row"] * row_count["n"]
        if selector == VENDOR_RESULT_ROW_SELECTOR:
            vendor_calls["n"] += 1
            # First read (pre-filter baseline) differs from the rest, so
            # the retry-until-changed read doesn't waste real sleep time.
            return ["UNFILTERED VENDOR " + str(i) for i in range(98)] if vendor_calls["n"] == 1 else ["Jensen Tire & Auto"]
        return []

    driver.on_click = on_click
    driver.on_find_all_text = on_find_all_text

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
    assert COST_ADDS_CLOSE_SELECTOR in driver.clicks
    assert SAVE_CONFIRMATION_YES_SELECTOR in driver.clicks
    # Must click Add New first to get a definitely-fresh row, not trust
    # whatever the default last row already held.
    assert driver.clicks.count(MODAL_OPEN_SELECTOR) == 1


def test_cost_add_retries_add_new_when_row_count_does_not_increase(fake_driver_cost_add_duplicate_memo):
    """Add New's click is verified, not trusted — if the row count never
    goes up, it must retry rather than silently filling a stale row."""
    from dealercenter_agent.tools.cost_add_tool import ROW_SELECTOR

    driver = fake_driver_cost_add_duplicate_memo

    def on_find_all_text(selector: str) -> list[str]:
        if selector == ROW_SELECTOR:
            return ["row"] * 5  # never increases, no matter how many times Add New is clicked
        return []

    driver.on_find_all_text = on_find_all_text

    with pytest.raises(StepFailure) as exc:
        CostAddTool().run(
            driver,
            category="Repair",
            cost=Decimal("10"),
            invoice_date=date(2026, 1, 1),
            vendor_name="Jensen",
            memo="X",
            skip_duplicate_check=True,
        )
    assert "did not append a new row" in exc.value.reason
    assert driver.clicks.count(MODAL_OPEN_SELECTOR) == 3


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
