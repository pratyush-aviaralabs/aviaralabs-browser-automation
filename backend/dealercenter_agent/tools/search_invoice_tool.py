from ..automation.base import BrowserDriver
from .base import StepFailure, Tool, ToolResult, ToolStatus

SEARCH_BOX_SELECTOR = "input[type='search'], input[placeholder*='Search' i]"

# Best-guess placeholders, unverified against the live search dropdown's
# real DOM (only seen in screenshots so far, same situation the POC was
# in before its selectors got corrected against the real page). These are
# the one place to fix once the actual markup is confirmed.
INVENTORY_ROW_SELECTOR = "[data-section='inventory'] .search-result-row"
DEAL_ROW_SELECTOR = "[data-section='deal'] .search-result-row"
# Confirms the click actually landed on the vehicle detail page (where
# Cost Info / Cost Adds live) — also a placeholder.
VEHICLE_DETAIL_LOADED_SELECTOR = "text=/Cost Info/i"


class SearchInvoiceTool(Tool):
    """Two sequential search tiers: VIN last-6 first, then stock number —
    each tier checks the Inventory block, falling back to the Deal block
    in the same dropdown, exactly like the existing dealercenter-cost-adds
    skill. Ambiguous (>1 row in either block) stops the search immediately
    rather than falling through to the next tier — only a clean zero-match
    tier is allowed to fall through. "not found in inventory" is the exact
    string the existing skill's callback contract requires.

    A single Inventory match gets clicked into (landing on the vehicle
    detail page CostAddTool needs) — a Deal match is reported but NOT
    clicked into yet; that navigation path is a separate, not-yet-built
    piece of work."""

    name = "SearchInvoiceTool"

    def run(
        self,
        driver: BrowserDriver,
        vin: str | None = None,
        stock_number: str | None = None,
        **kwargs,
    ) -> ToolResult:
        if vin:
            result = self._search_once(driver, vin[-6:])
            if result is not None:
                return result

        if stock_number:
            result = self._search_once(driver, stock_number)
            if result is not None:
                return result

        raise StepFailure(self.name, "not found in inventory")

    def _search_once(self, driver: BrowserDriver, query: str) -> ToolResult | None:
        driver.fill(SEARCH_BOX_SELECTOR, query)

        inventory_rows = driver.find_all_text(INVENTORY_ROW_SELECTOR)
        if len(inventory_rows) > 1:
            raise StepFailure(
                self.name,
                f"ambiguous match: {len(inventory_rows)} Inventory rows for query {query!r}",
            )
        if len(inventory_rows) == 1:
            driver.click(INVENTORY_ROW_SELECTOR)
            if not driver.wait_visible(VEHICLE_DETAIL_LOADED_SELECTOR, timeout_ms=15000):
                raise StepFailure(
                    self.name, "clicked Inventory match but vehicle detail page did not load"
                )
            return ToolResult(
                status=ToolStatus.SUCCESS,
                data={"match_type": "inventory", "row_text": inventory_rows[0], "query": query},
            )

        deal_rows = driver.find_all_text(DEAL_ROW_SELECTOR)
        if len(deal_rows) > 1:
            raise StepFailure(
                self.name,
                f"ambiguous match: {len(deal_rows)} Deal rows for query {query!r}",
            )
        if len(deal_rows) == 1:
            return ToolResult(
                status=ToolStatus.SUCCESS,
                data={"match_type": "deal", "row_text": deal_rows[0], "query": query},
            )

        return None
