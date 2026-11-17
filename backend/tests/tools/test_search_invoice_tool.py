import pytest

from dealercenter_agent.tools.base import StepFailure, ToolStatus
from dealercenter_agent.tools.search_invoice_tool import (
    DEAL_CONTRACT_IFRAME_SELECTOR,
    SEARCH_BOX_SELECTOR,
    SEARCH_RESULT_ROW_SELECTOR,
    SearchInvoiceTool,
)


def test_search_finds_single_inventory_match_and_clicks_into_it(fake_driver_one_inventory_match):
    result = SearchInvoiceTool().run(
        fake_driver_one_inventory_match, vin="1FTFW1E81NFAG16336", stock_number=None
    )
    assert result.status == ToolStatus.SUCCESS
    assert result.data["match_type"] == "inventory"
    assert fake_driver_one_inventory_match.filled[SEARCH_BOX_SELECTOR] == "G16336"
    assert SEARCH_RESULT_ROW_SELECTOR in fake_driver_one_inventory_match.clicks


def test_search_waits_for_dropdown_before_reading_it(fake_driver_dropdown_renders_late):
    # Must NOT read find_all_text() before waiting for the dropdown to
    # actually render — see the fixture's docstring for the live bug this
    # models.
    result = SearchInvoiceTool().run(
        fake_driver_dropdown_renders_late, vin="1FTFW1E81NFAG16336", stock_number=None
    )
    assert result.status == ToolStatus.SUCCESS
    assert result.data["match_type"] == "inventory"


def test_search_finds_single_deal_match_and_clicks_through_to_edit_vehicle(fake_driver_only_deal_match):
    """Confirmed live (2026-11-18): Inventory and Deal rows share one
    CSS selector — classified by "Deal #:" in the text, not a second
    selector. A single Deal match now clicks through: Deal row ->
    "Deal Details" modal -> vehicle link -> "Edit Vehicle" modal."""
    result = SearchInvoiceTool().run(
        fake_driver_only_deal_match, vin="1FTFW1E81NFAG16336", stock_number=None
    )
    assert result.status == ToolStatus.SUCCESS
    assert result.data["match_type"] == "deal"
    assert SEARCH_RESULT_ROW_SELECTOR in fake_driver_only_deal_match.clicks
    # Confirmed live (2026-11-18): the ENTIRE Edit Vehicle surface
    # (Cost Adds, vendor search, Save Confirmation) lives inside this
    # iframe too, not just its own title text — every downstream Tool
    # needs the driver switched into this context to work at all.
    assert fake_driver_only_deal_match.frame_selector == DEAL_CONTRACT_IFRAME_SELECTOR


def test_search_inventory_match_does_not_enter_an_iframe_context(fake_driver_one_inventory_match):
    result = SearchInvoiceTool().run(
        fake_driver_one_inventory_match, vin="1FTFW1E81NFAG16336", stock_number=None
    )
    assert result.data["match_type"] == "inventory"
    assert fake_driver_one_inventory_match.frame_selector is None


def test_search_fails_when_deal_details_modal_never_opens(fake_driver_deal_details_never_opens):
    with pytest.raises(StepFailure) as exc:
        SearchInvoiceTool().run(
            fake_driver_deal_details_never_opens, vin="1FTFW1E81NFAG16336", stock_number=None
        )
    assert "Deal Details modal did not open" in exc.value.reason


def test_search_fails_when_edit_vehicle_modal_never_opens(fake_driver_edit_vehicle_never_opens):
    with pytest.raises(StepFailure) as exc:
        SearchInvoiceTool().run(
            fake_driver_edit_vehicle_never_opens, vin="1FTFW1E81NFAG16336", stock_number=None
        )
    assert "Edit Vehicle modal did not open" in exc.value.reason


def test_search_holds_without_clicking_when_both_inventory_and_deal_match(
    fake_driver_both_inventory_and_deal_match,
):
    """Confirmed required live (2026-11-18): both an Inventory and a
    Deal match for the same query must hold — report both, click into
    neither — not silently prefer one."""
    driver = fake_driver_both_inventory_and_deal_match
    with pytest.raises(StepFailure) as exc:
        SearchInvoiceTool().run(driver, vin="1FTFW1E81NFAG16336", stock_number=None)
    assert "both an Inventory match and a Deal match" in exc.value.reason
    assert driver.clicks == []


def test_search_falls_back_to_stock_number_when_vin_tier_finds_nothing(
    fake_driver_falls_back_to_stock_number,
):
    result = SearchInvoiceTool().run(
        fake_driver_falls_back_to_stock_number, vin="1FTFW1E81NFAZZZZZZ", stock_number="22421"
    )
    assert result.status == ToolStatus.SUCCESS
    assert result.data["match_type"] == "inventory"
    assert result.data["query"] == "22421"


def test_search_fails_distinctly_on_no_match(fake_driver_no_matches):
    with pytest.raises(StepFailure) as exc:
        SearchInvoiceTool().run(fake_driver_no_matches, vin="ZZZZZZ", stock_number=None)
    assert exc.value.reason == "not found in inventory"


def test_search_fails_distinctly_on_ambiguous_match(fake_driver_two_inventory_matches):
    with pytest.raises(StepFailure) as exc:
        SearchInvoiceTool().run(
            fake_driver_two_inventory_matches, vin="1FTFW1E81NFAG16336", stock_number=None
        )
    assert "ambiguous" in exc.value.reason


def test_search_raises_when_no_search_key_given(fake_driver_no_matches):
    with pytest.raises(StepFailure) as exc:
        SearchInvoiceTool().run(fake_driver_no_matches, vin=None, stock_number=None)
    assert exc.value.reason == "not found in inventory"
