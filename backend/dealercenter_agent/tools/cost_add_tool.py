from datetime import date
from decimal import Decimal

from ..automation.base import BrowserDriver
from .base import StepFailure, Tool, ToolResult, ToolStatus

VALID_CATEGORIES = ("Repair", "Other")

# COST_ADDS_LINK_SELECTOR confirmed against the real DOM via console
# dump (2026-11-17): the dollar-amount next to the "Cost Adds" label is
# a Kendo <button fillmode="link"> containing a <span class="k-button-
# text">, NOT an <a> tag — the original guess looked for the wrong
# element type entirely. Still anchored by XPath to the "Cost Adds" text
# since the amount itself is vehicle-specific.
#
# Everything below this line in the Cost Adds modal (row fields, vendor
# search, Save) is still unverified — not reached yet.
COST_ADDS_LINK_SELECTOR = "text=/Cost Adds/i >> xpath=following::button[1]"
# Confirmed visible in the real modal (2026-11-17) — used purely to
# confirm the modal opened, since the exact new-row field selectors
# below are still unverified.
MODAL_OPEN_SELECTOR = "button:has-text('Add New')"

# Everything below confirmed against the real DOM via console dump
# (2026-11-17): this is NOT a plain Kendo grid with data-field cells —
# it's an Angular reactive form, each cell identified by
# formcontrolname (subCategoryID, amount, referenceNumber,
# vendorPartnerId/searchText, dateAdded, memo). There's always exactly
# one blank, already-rendered row at the end of the table ready to
# fill in — "Add New" appends an ADDITIONAL blank row on top of that,
# it is never required for the first (or only) entry, confirmed live
# across multiple vehicles. No data-field attributes exist anywhere in
# this table; the earlier guesses were a different grid style entirely.
#
# The Description column's <kendo-combobox> has NO formcontrolname —
# confirmed every existing row shows Description == Category (e.g.
# "Repair"/"Repair", "Parts"/"Parts"), so Description auto-fills from
# the Category selection and is never set directly.
# The Category <kendo-combobox>'s own searchbar <input> is readonly and
# a plain click() on it does not reliably trigger Kendo's popup-open
# behavior (confirmed live 2026-11-18: automated click() on the input
# left the dropdown closed, 30s get_by_text timeout; a manual click —
# presumably landing on the component's own toggle button — opened it
# fine). Target that dedicated toggle button instead.
NEW_ROW_CATEGORY_SELECTOR = "tr:last-child .category-col button[aria-label='Select']"
NEW_ROW_COST_SELECTOR = "tr:last-child .cost-col input.k-input-inner"
NEW_ROW_DATE_SELECTOR = "tr:last-child .date-added-col input.k-input-inner"
NEW_ROW_VENDOR_SEARCH_ICON_SELECTOR = "tr:last-child .vendor-col button[title='Search']"
# The "Search or Add Vendor" dialog this button opens, and its result
# row, are still UNVERIFIED — not reached yet, needs its own live
# console check before this path is trusted.
VENDOR_SEARCH_INPUT_SELECTOR = "[role='dialog'] input[placeholder*='Search' i]"
VENDOR_RESULT_ROW_SELECTOR = "[role='dialog'] .vendor-search-result-row"
NEW_ROW_MEMO_SELECTOR = "tr:last-child input[formcontrolname='memo']"
# All rows' Memo cells (existing entries, unlike NEW_ROW_MEMO_SELECTOR
# which only targets the blank last row) — read before adding anything,
# per the existing skill's duplicate-invoice rule. Must be read with
# find_all_values, NOT find_all_text: this is an <input>, so its value
# lives in the `value` property, not as visible text — find_all_text's
# inner-text read returns "" here regardless of selector.
EXISTING_MEMO_SELECTOR = "input[formcontrolname='memo']"
SAVE_BUTTON_SELECTOR = "button.save-btn"


def _normalize_memo(text: str) -> str:
    return text.replace(" ", "")


class CostAddTool(Tool):
    """Opens the Cost Adds modal (from the vehicle detail page
    SearchInvoiceTool lands on). First checks every existing row's Memo
    for an exact match (ignoring spaces) against `memo` — matching the
    existing dealercenter-cost-adds skill's rule exactly ("Exact
    invoice_number match, ignore spaces... Do not Add New, fill, upload,
    or Save"). Only if nothing matches does it fill the grid's blank
    new-entry row, after confirming that row's Memo is actually blank
    (guards against writing into existing data if an assumption about
    row count/selectors ever breaks).

    Cost, Category, and Memo are required — any failure filling these is
    a real StepFailure. Vendor is best-effort: search-and-select, never
    invented, but a no-match or ambiguous result is logged as a warning
    in the returned data rather than aborting the entry. Description has
    no control of its own — it mirrors whatever Category is set to."""

    name = "CostAddTool"

    def run(
        self,
        driver: BrowserDriver,
        category: str,
        cost: Decimal,
        invoice_date: date,
        vendor_name: str,
        memo: str,
        skip_duplicate_check: bool = False,
        **kwargs,
    ) -> ToolResult:
        if category not in VALID_CATEGORIES:
            raise StepFailure(
                self.name, f"unknown category {category!r} (expected one of {VALID_CATEGORIES})"
            )

        driver.click(COST_ADDS_LINK_SELECTOR)
        if not driver.wait_visible(MODAL_OPEN_SELECTOR, timeout_ms=10000):
            raise StepFailure(self.name, "Cost Adds modal did not open")

        # skip_duplicate_check exists ONLY to let a human deliberately
        # force the write path in manual testing (e.g. verifying the
        # Add-entry flow against an invoice that's already present).
        # Must stay False by default — every normal call, including the
        # pipeline, goes through the real duplicate check.
        if not skip_duplicate_check:
            existing_memos = driver.find_all_values(EXISTING_MEMO_SELECTOR)
            target = _normalize_memo(memo)
            if any(_normalize_memo(m) == target for m in existing_memos if m.strip()):
                return ToolResult(status=ToolStatus.EXISTS, data={"memo": memo})
        else:
            # Forcing the write path deliberately (manual testing only) —
            # click Add New first so we fill a fresh, definitely-blank
            # row instead of trusting whatever the default last row
            # currently holds. NEW_ROW_* selectors target tr:last-child,
            # which becomes this new row once it's appended.
            driver.click(MODAL_OPEN_SELECTOR)

        # Guard against writing into a row that already has data — relying
        # on tr:last-child always being the fresh blank row is only as
        # good as that assumption holding. A real new row's Memo is
        # always blank; any value there means something upstream (row
        # count, a stale selector, Add New not actually landing a new
        # row) didn't go the way we expect, so stop instead of risking a
        # write into existing data.
        row_memo = driver.find_all_values(NEW_ROW_MEMO_SELECTOR)
        if any(v.strip() for v in row_memo):
            raise StepFailure(
                self.name,
                f"expected a blank new row but its Memo already has a value: {row_memo!r}",
            )

        # Cost, Category, and Memo are the fields this entry must have —
        # any failure here is a real StepFailure. Description has no
        # formcontrolname of its own (every existing row shows it
        # mirroring Category, confirmed live), so setting Category alone
        # is enough. Vendor is best-effort below: logged and skipped on
        # failure rather than aborting the whole entry, per instruction.
        driver.select_option(NEW_ROW_CATEGORY_SELECTOR, category)
        driver.fill(NEW_ROW_COST_SELECTOR, f"{cost:.2f}")
        driver.fill(NEW_ROW_DATE_SELECTOR, invoice_date.strftime("%m/%d/%Y"))

        vendor_warning = self._select_vendor(driver, vendor_name)

        driver.fill(NEW_ROW_MEMO_SELECTOR, memo)
        driver.click(SAVE_BUTTON_SELECTOR)

        data = {"category": category, "vendor": vendor_name}
        if vendor_warning:
            data["warnings"] = [vendor_warning]
            print(f"[{self.name}] WARNING: {vendor_warning}")
        return ToolResult(status=ToolStatus.SUCCESS, data=data)

    def _select_vendor(self, driver: BrowserDriver, vendor_name: str) -> str | None:
        """Best-effort: Cost/Category/Memo are the required fields, Vendor
        is not — on any failure here, log a warning and leave the row's
        Vendor unset rather than aborting the whole entry. Still never
        invents a vendor match; it only skips setting one."""
        query = vendor_name.split()[0] if vendor_name else ""

        try:
            driver.click(NEW_ROW_VENDOR_SEARCH_ICON_SELECTOR)
            if not driver.wait_visible(VENDOR_SEARCH_INPUT_SELECTOR, timeout_ms=5000):
                return "vendor search box did not open — Vendor left unset"
            driver.fill(VENDOR_SEARCH_INPUT_SELECTOR, query)

            results = driver.find_all_text(VENDOR_RESULT_ROW_SELECTOR)
            if not results:
                return f"no vendor match for {vendor_name!r} — Vendor left unset"
            if len(results) > 1:
                return (
                    f"ambiguous vendor match: {len(results)} results for "
                    f"{vendor_name!r} — Vendor left unset"
                )

            driver.click(VENDOR_RESULT_ROW_SELECTOR)
            return None
        except Exception as exc:
            return f"vendor selection failed ({exc}) — Vendor left unset"
