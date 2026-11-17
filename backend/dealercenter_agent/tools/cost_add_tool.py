from datetime import date
from decimal import Decimal

from ..automation.base import BrowserDriver
from .base import StepFailure, Tool, ToolResult, ToolStatus

VALID_CATEGORIES = ("Repair", "Other")

# Best-guess placeholders, unverified against the live Cost Adds modal's
# real DOM (only seen in screenshots so far) — same situation
# SearchInvoiceTool's row selectors were in before being corrected
# against the real page. These are the one place to fix once the actual
# markup is confirmed.
COST_ADDS_LINK_SELECTOR = "text=/Cost Adds/i >> xpath=following::a[1]"
NEW_ROW_DESCRIPTION_SELECTOR = ".k-grid-table tr:last-child [data-field='Description']"
NEW_ROW_CATEGORY_SELECTOR = ".k-grid-table tr:last-child [data-field='Category']"
NEW_ROW_COST_SELECTOR = ".k-grid-table tr:last-child [data-field='Cost'] input"
NEW_ROW_DATE_SELECTOR = ".k-grid-table tr:last-child [data-field='DateAdded'] input"
NEW_ROW_VENDOR_SEARCH_ICON_SELECTOR = ".k-grid-table tr:last-child .vendor-search-icon"
VENDOR_SEARCH_INPUT_SELECTOR = "[role='dialog'] input[placeholder*='Search' i]"
VENDOR_RESULT_ROW_SELECTOR = "[role='dialog'] .vendor-search-result-row"
NEW_ROW_MEMO_SELECTOR = ".k-grid-table tr:last-child [data-field='Memo'] input"
# All rows' Memo cells (existing entries, unlike NEW_ROW_MEMO_SELECTOR
# which only targets the blank last row) — read before adding anything,
# per the existing skill's duplicate-invoice rule.
EXISTING_MEMO_SELECTOR = ".k-grid-table [data-field='Memo']"
SAVE_BUTTON_SELECTOR = "button:has-text('Save')"


def _normalize_memo(text: str) -> str:
    return text.replace(" ", "")


class CostAddTool(Tool):
    """Opens the Cost Adds modal (from the vehicle detail page
    SearchInvoiceTool lands on). First checks every existing row's Memo
    for an exact match (ignoring spaces) against `memo` — matching the
    existing dealercenter-cost-adds skill's rule exactly ("Exact
    invoice_number match, ignore spaces... Do not Add New, fill, upload,
    or Save"). Only if nothing matches does it fill the grid's blank
    new-entry row: Description/Category both set to the given category
    (pre-classified by ClassifyCategoryTool — this Tool trusts whatever
    category string it's handed, it doesn't re-derive one), Cost, Date
    Added, Vendor (search-and-select, never invented), and Memo. Then
    Save."""

    name = "CostAddTool"

    def run(
        self,
        driver: BrowserDriver,
        category: str,
        cost: Decimal,
        invoice_date: date,
        vendor_name: str,
        memo: str,
        **kwargs,
    ) -> ToolResult:
        if category not in VALID_CATEGORIES:
            raise StepFailure(
                self.name, f"unknown category {category!r} (expected one of {VALID_CATEGORIES})"
            )

        driver.click(COST_ADDS_LINK_SELECTOR)
        if not driver.wait_visible(NEW_ROW_DESCRIPTION_SELECTOR, timeout_ms=10000):
            raise StepFailure(self.name, "Cost Adds modal did not open")

        existing_memos = driver.find_all_text(EXISTING_MEMO_SELECTOR)
        target = _normalize_memo(memo)
        if any(_normalize_memo(m) == target for m in existing_memos if m.strip()):
            return ToolResult(status=ToolStatus.EXISTS, data={"memo": memo})

        driver.select_option(NEW_ROW_DESCRIPTION_SELECTOR, category)
        driver.select_option(NEW_ROW_CATEGORY_SELECTOR, category)
        driver.fill(NEW_ROW_COST_SELECTOR, f"{cost:.2f}")
        driver.fill(NEW_ROW_DATE_SELECTOR, invoice_date.strftime("%m/%d/%Y"))

        self._select_vendor(driver, vendor_name)

        driver.fill(NEW_ROW_MEMO_SELECTOR, memo)
        driver.click(SAVE_BUTTON_SELECTOR)

        return ToolResult(status=ToolStatus.SUCCESS, data={"category": category, "vendor": vendor_name})

    def _select_vendor(self, driver: BrowserDriver, vendor_name: str) -> None:
        # Matches the existing skill's rule: search by the first word of
        # the vendor name, pick the closest legal name — never invent one.
        query = vendor_name.split()[0] if vendor_name else ""

        driver.click(NEW_ROW_VENDOR_SEARCH_ICON_SELECTOR)
        if not driver.wait_visible(VENDOR_SEARCH_INPUT_SELECTOR, timeout_ms=5000):
            raise StepFailure(self.name, "vendor search box did not open")
        driver.fill(VENDOR_SEARCH_INPUT_SELECTOR, query)

        results = driver.find_all_text(VENDOR_RESULT_ROW_SELECTOR)
        if not results:
            raise StepFailure(self.name, f"no vendor match for {vendor_name!r} — not inventing one")
        if len(results) > 1:
            raise StepFailure(
                self.name, f"ambiguous vendor match: {len(results)} results for {vendor_name!r}"
            )

        driver.click(VENDOR_RESULT_ROW_SELECTOR)
