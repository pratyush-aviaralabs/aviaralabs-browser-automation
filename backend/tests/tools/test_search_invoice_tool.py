import pytest

from dealercenter_agent.tools.base import StepFailure, ToolStatus
from dealercenter_agent.tools.search_invoice_tool import SEARCH_BOX_SELECTOR, SearchInvoiceTool


def test_search_finds_single_inventory_match(fake_driver_one_inventory_match):
    result = SearchInvoiceTool().run(
        fake_driver_one_inventory_match, vin="1FTFW1E81NFAG16336", po_number=None, stock_number=None
    )
    assert result.status == ToolStatus.SUCCESS
    assert result.data["match_type"] == "inventory"
    assert fake_driver_one_inventory_match.filled[SEARCH_BOX_SELECTOR] == "G16336"


def test_search_falls_back_to_deal_block_when_no_inventory_row(fake_driver_only_deal_match):
    result = SearchInvoiceTool().run(
        fake_driver_only_deal_match, vin="1FTFW1E81NFAG16336", po_number=None, stock_number=None
    )
    assert result.data["match_type"] == "deal"


def test_search_uses_stock_number_when_no_vin_or_po(fake_driver_one_inventory_match):
    result = SearchInvoiceTool().run(
        fake_driver_one_inventory_match, vin=None, po_number=None, stock_number="22421"
    )
    assert result.status == ToolStatus.SUCCESS
    assert fake_driver_one_inventory_match.filled[SEARCH_BOX_SELECTOR] == "22421"


def test_search_fails_distinctly_on_no_match(fake_driver_no_matches):
    with pytest.raises(StepFailure) as exc:
        SearchInvoiceTool().run(fake_driver_no_matches, vin="ZZZZZZ", po_number=None, stock_number=None)
    assert exc.value.reason == "not found in inventory"


def test_search_fails_distinctly_on_ambiguous_match(fake_driver_two_inventory_matches):
    with pytest.raises(StepFailure) as exc:
        SearchInvoiceTool().run(
            fake_driver_two_inventory_matches, vin="1FTFW1E81NFAG16336", po_number=None, stock_number=None
        )
    assert "ambiguous" in exc.value.reason


def test_search_raises_when_no_search_key_given(fake_driver_no_matches):
    with pytest.raises(StepFailure) as exc:
        SearchInvoiceTool().run(fake_driver_no_matches, vin=None, po_number=None, stock_number=None)
    assert "no search key" in exc.value.reason
