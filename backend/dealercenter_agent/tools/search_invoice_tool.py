import time

from ..automation.base import BrowserDriver
from .base import StepFailure, Tool, ToolResult, ToolStatus

SEARCH_BOX_SELECTOR = "input[type='search'], input[placeholder*='Search' i]"

# INVENTORY_ROW_SELECTOR confirmed against the real DOM (console dump,
# 2026-11-17): each Inventory match renders as exactly one
# <div class="search-result-item ..."> inside the Inventory section.
INVENTORY_ROW_SELECTOR = ".search-result-item"
# DEAL_ROW_SELECTOR is still an unverified placeholder — we haven't seen
# a live Deal match yet. CAUTION if/when fixing this: if Deal rows turn
# out to reuse the same generic .search-result-item class (plausible,
# since it looks like one shared Angular component), this selector MUST
# be scoped to the Deal section specifically, or it'll double-count the
# same elements INVENTORY_ROW_SELECTOR already counted.
DEAL_ROW_SELECTOR = "[data-section='deal'] .search-result-row"
# Confirms the click actually landed on the vehicle detail page (where
# Cost Info / Cost Adds live) — also a placeholder.
VEHICLE_DETAIL_LOADED_SELECTOR = "text=/Cost Info/i"


def _read_rows_with_retry(driver: BrowserDriver, selector: str, attempts: int = 3, delay_ms: int = 300) -> list[str]:
    """Confirmed live (2026-11-17): wait_visible() can succeed, and the
    very next find_all_text() call still reads empty — a brief re-render
    gap in Angular's reactive list, not a wrong selector (the same
    selector read correctly a second later via manual console check).
    A single read right after a wait isn't enough on its own; retry the
    read itself a few times before trusting 'nothing here'."""
    for attempt in range(attempts):
        rows = driver.find_all_text(selector)
        if rows:
            return rows
        if attempt < attempts - 1:
            time.sleep(delay_ms / 1000)
    return []


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

        # The dropdown renders asynchronously after typing — find_all_text()
        # is a single-shot read, not a poll, so calling it immediately after
        # fill() races the render and reads an empty dropdown every time,
        # regardless of whether the row selectors are correct. Wait for
        # EITHER row type to actually appear (or genuinely time out) before
        # reading either result set. Same bug class as LoginTool's redirect
        # race and dismiss_blocking_modals' async-modal race.
        driver.wait_visible(f"{INVENTORY_ROW_SELECTOR}, {DEAL_ROW_SELECTOR}", timeout_ms=8000)

        inventory_rows = _read_rows_with_retry(driver, INVENTORY_ROW_SELECTOR)
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

        deal_rows = _read_rows_with_retry(driver, DEAL_ROW_SELECTOR)
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
