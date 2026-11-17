import time

from ..automation.base import BrowserDriver
from .base import StepFailure, Tool, ToolResult, ToolStatus

SEARCH_BOX_SELECTOR = "input[type='search'], input[placeholder*='Search' i]"

# Confirmed live (2026-11-18): Inventory AND Deal rows share the EXACT
# same CSS class — there is NOT a separate per-type selector the way
# DEAL_ROW_SELECTOR previously (wrongly) assumed. A Deal row's text
# always contains "Deal #:" (e.g. "Deal #: 16228 2022 FORD F150
# LIGHTNING Last 6 VIN: G16336 GENE MATTEW PARKER ... Status: Sold -
# Funded"); an Inventory row's never does. Classify by content, not by
# a second selector.
SEARCH_RESULT_ROW_SELECTOR = ".search-result-item"
DEAL_TEXT_MARKER = "Deal #:"
# Confirms the click actually landed on the vehicle detail page (where
# Cost Info / Cost Adds live) — confirmed live.
VEHICLE_DETAIL_LOADED_SELECTOR = "text=/Cost Info/i"
# Confirmed live (2026-11-18): clicking a Deal row opens a "Deal
# Details" modal (title "Deal Details - <id> <customer name>").
DEAL_DETAILS_MODAL_SELECTOR = "text=/Deal Details/i"
# Confirmed live (2026-11-18): the Deal Details dialog's own content
# (where the vehicle quick-link lives) is rendered inside an
# <iframe id="dc-iframe-dialog"> — a plain page-level selector can
# never reach it regardless of correctness; Playwright requires an
# explicit frame-scoped locator (see PlaywrightDriver._locator's
# "iframe=" convention). The link itself: <a
# dcuisharedstopclickpropagation href="#" target="_blank"
# title="<dynamic vehicle name>"><span class="d-block"><dynamic
# vehicle name></span></a> — anchored on the custom
# dcuisharedstopclickpropagation attribute specifically because the
# vehicle name/title text is dynamic per deal.
DEAL_CONTRACT_IFRAME_SELECTOR = "#dc-iframe-dialog"
DEAL_DETAILS_VEHICLE_LINK_SELECTOR = f"iframe={DEAL_CONTRACT_IFRAME_SELECTOR} >> a[dcuisharedstopclickpropagation]"
# UNVERIFIED — clicking that link opens a further "Edit Vehicle" modal,
# functionally identical in structure to the Inventory page's own
# vehicle detail view (same Cost Info/Cost Adds section, same tabs:
# Vehicle Info, Marketing, Media, ..., Files), so CostAddTool/
# FileUploadTool should work against it unchanged once reached — but
# this confirmation selector itself hasn't been verified live yet.
EDIT_VEHICLE_MODAL_SELECTOR = "text=/Edit Vehicle/i"


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


def _is_deal_row(text: str) -> bool:
    return DEAL_TEXT_MARKER in text


class SearchInvoiceTool(Tool):
    """Two sequential search tiers: VIN last-6 first, then stock number.
    Each tier reads every result row (Inventory and Deal share one
    selector, confirmed live — see SEARCH_RESULT_ROW_SELECTOR) and
    classifies by content. "not found in inventory" is the exact
    string the existing skill's callback contract requires.

    - Exactly one Inventory match, no Deal match: click through,
      landing on the vehicle detail page (unchanged from before).
    - Exactly one Deal match, no Inventory match: click through the
      Deal -> Deal Details modal -> vehicle link -> Edit Vehicle modal,
      which is functionally the same vehicle-detail surface
      CostAddTool/FileUploadTool already work against.
    - Both an Inventory AND a Deal match for the same query: hold —
      report both, click into neither. Resolving that ambiguity needs
      a human, same as the existing ">1 match" ambiguous case.
    - More than one match within either category: ambiguous, same as
      before."""

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
        # regardless of whether the row selector is correct. Same bug class
        # as LoginTool's redirect race and dismiss_blocking_modals' async-
        # modal race.
        driver.wait_visible(SEARCH_RESULT_ROW_SELECTOR, timeout_ms=8000)
        all_rows = _read_rows_with_retry(driver, SEARCH_RESULT_ROW_SELECTOR)

        inventory_rows = [r for r in all_rows if not _is_deal_row(r)]
        deal_rows = [r for r in all_rows if _is_deal_row(r)]

        if len(inventory_rows) > 1:
            raise StepFailure(
                self.name,
                f"ambiguous match: {len(inventory_rows)} Inventory rows for query {query!r}",
            )
        if len(deal_rows) > 1:
            raise StepFailure(
                self.name,
                f"ambiguous match: {len(deal_rows)} Deal rows for query {query!r}",
            )

        if inventory_rows and deal_rows:
            raise StepFailure(
                self.name,
                f"found both an Inventory match and a Deal match for query {query!r} — "
                f"holding, no action taken: inventory={inventory_rows[0]!r} deal={deal_rows[0]!r}",
            )

        if len(inventory_rows) == 1:
            driver.click(SEARCH_RESULT_ROW_SELECTOR)
            if not driver.wait_visible(VEHICLE_DETAIL_LOADED_SELECTOR, timeout_ms=15000):
                raise StepFailure(
                    self.name, "clicked Inventory match but vehicle detail page did not load"
                )
            return ToolResult(
                status=ToolStatus.SUCCESS,
                data={"match_type": "inventory", "row_text": inventory_rows[0], "query": query},
            )

        if len(deal_rows) == 1:
            driver.click(SEARCH_RESULT_ROW_SELECTOR)
            if not driver.wait_visible(DEAL_DETAILS_MODAL_SELECTOR, timeout_ms=15000):
                raise StepFailure(self.name, "clicked Deal match but Deal Details modal did not open")
            # force_click, not click — confirmed live (2026-11-18): the
            # locator resolves to exactly the right <a> (confirmed via
            # Playwright's own log), but its plain visibility check
            # disagrees with what's actually on screen — same class of
            # Playwright-heuristic-vs-real-rendering mismatch already
            # hit with the Cost Adds Save button, likely because this
            # <a> wraps a <span class="d-block"> child rather than
            # being block-level itself.
            driver.force_click(DEAL_DETAILS_VEHICLE_LINK_SELECTOR)
            if not driver.wait_visible(EDIT_VEHICLE_MODAL_SELECTOR, timeout_ms=15000):
                raise StepFailure(
                    self.name, "clicked the vehicle link in Deal Details but Edit Vehicle modal did not open"
                )
            return ToolResult(
                status=ToolStatus.SUCCESS,
                data={"match_type": "deal", "row_text": deal_rows[0], "query": query},
            )

        return None
