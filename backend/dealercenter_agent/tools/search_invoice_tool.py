from ..automation.base import BrowserDriver
from .base import StepFailure, Tool, ToolResult, ToolStatus

SEARCH_BOX_SELECTOR = "input[type='search'], input[placeholder*='Search' i]"

# Best-guess placeholders, unverified against the live search dropdown's
# real DOM (only seen in screenshots so far, same situation the POC was
# in before its selectors got corrected against the real page). These are
# the one place to fix once the actual markup is confirmed.
INVENTORY_ROW_SELECTOR = "[data-section='inventory'] .search-result-row"
DEAL_ROW_SELECTOR = "[data-section='deal'] .search-result-row"


class SearchInvoiceTool(Tool):
    """Mirrors the existing dealercenter-cost-adds skill's exact search
    rule: VIN last-6 first, then PO, then stock number. Reads the
    Inventory block; if nothing there, reads the Deal block in the same
    dropdown — never clicks into a result. Ambiguous (>1 row in either
    block) and not-found are two distinct, never-guessed failure reasons;
    "not found in inventory" is the exact string the existing skill's
    callback contract requires."""

    name = "SearchInvoiceTool"

    def run(
        self,
        driver: BrowserDriver,
        vin: str | None = None,
        po_number: str | None = None,
        stock_number: str | None = None,
        **kwargs,
    ) -> ToolResult:
        query = self._resolve_query(vin, po_number, stock_number)

        driver.fill(SEARCH_BOX_SELECTOR, query)

        inventory_rows = driver.find_all_text(INVENTORY_ROW_SELECTOR)
        if len(inventory_rows) == 1:
            return ToolResult(
                status=ToolStatus.SUCCESS,
                data={"match_type": "inventory", "row_text": inventory_rows[0], "query": query},
            )
        if len(inventory_rows) > 1:
            raise StepFailure(
                self.name,
                f"ambiguous match: {len(inventory_rows)} Inventory rows for query {query!r}",
            )

        deal_rows = driver.find_all_text(DEAL_ROW_SELECTOR)
        if len(deal_rows) == 1:
            return ToolResult(
                status=ToolStatus.SUCCESS,
                data={"match_type": "deal", "row_text": deal_rows[0], "query": query},
            )
        if len(deal_rows) > 1:
            raise StepFailure(
                self.name,
                f"ambiguous match: {len(deal_rows)} Deal rows for query {query!r}",
            )

        raise StepFailure(self.name, "not found in inventory")

    def _resolve_query(
        self, vin: str | None, po_number: str | None, stock_number: str | None
    ) -> str:
        if vin:
            return vin[-6:]
        if po_number:
            return po_number
        if stock_number:
            return stock_number
        raise StepFailure(
            self.name, "no search key provided (vin, po_number, stock_number all empty)"
        )
