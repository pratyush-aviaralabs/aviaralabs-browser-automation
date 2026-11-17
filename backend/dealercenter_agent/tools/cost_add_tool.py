import re
import time
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
#
# CSS `tr:last-child` is NOT "the last row in the table" here — confirmed
# live (2026-11-18): find_all_values on a tr:last-child-scoped selector
# returned EVERY row's value (15 for 15 rows), not just the new one.
# Each <tr> is apparently rendered inside its own Angular host wrapper,
# so every row independently satisfies :last-child (each is the sole
# child of its own wrapper).
#
# A plain "(//tr)[last()]" (the true last <tr> in document order) is
# ALSO wrong — confirmed live (2026-11-18): the click on a selector
# built from it timed out entirely (locator never resolved), meaning it
# picked some unrelated <tr> elsewhere on the page. Angular/Kendo
# dialogs are commonly appended at the very end of <body>'s DOM order,
# so background page content can legitimately contain <tr> elements
# that sort AFTER this modal's own rows.
#
# formcontrolname="memo" is specific to Cost Adds rows (not expected to
# exist anywhere else on the page), so anchor off the last one of those
# instead and walk up to its row — unambiguous regardless of document
# order elsewhere.
LAST_ROW_XPATH = "xpath=(//input[@formcontrolname='memo'])[last()]/ancestor::tr[1]"
# Used only for the Add-New-worked row-count check — count memo inputs,
# not every <tr> on the page, for the same reason as above.
ROW_SELECTOR = "xpath=//input[@formcontrolname='memo']"
NEW_ROW_CATEGORY_SELECTOR = LAST_ROW_XPATH + "//td[contains(@class,'category-col')]//button[@aria-label='Select']"
NEW_ROW_COST_SELECTOR = LAST_ROW_XPATH + "//td[contains(@class,'cost-col')]//input[contains(@class,'k-input-inner')]"
NEW_ROW_DATE_SELECTOR = LAST_ROW_XPATH + "//td[contains(@class,'date-added-col')]//input[contains(@class,'k-input-inner')]"
NEW_ROW_VENDOR_SEARCH_ICON_SELECTOR = LAST_ROW_XPATH + "//td[contains(@class,'vendor-col')]//button[@title='Search']"
# Confirmed visible in every filled row's Vendor cell (the clickable
# vendor name, e.g. "Jensen Tire & Auto") — absent on a genuinely blank
# row, which shows the "Search Vendor" placeholder with no <a>. Used by
# the blank-row guard, not by the fill/select flow itself.
NEW_ROW_VENDOR_LINK_SELECTOR = LAST_ROW_XPATH + "//td[contains(@class,'vendor-col')]//a"
# The "Search or Add Vendor" dialog this button opens — confirmed live
# (2026-11-18), see selectors below.
#
# [role='dialog'] alone is ambiguous — confirmed live (2026-11-18): the
# Cost Adds dialog itself ALSO has role="dialog".
#
# "(//div[@role='dialog'])[last()]" (picking by document position) is
# ALSO wrong — confirmed live (2026-11-18): this only means "the vendor
# dialog" while it's actually open. Once it closes, "the last
# [role='dialog']" silently becomes the outer Cost Adds dialog itself —
# which also contains plenty of input.k-input-inner elements (every
# row's own Cost/Category inputs), so wait_visible(VENDOR_SEARCH_INPUT_
# SELECTOR) kept reporting "still open" even after the vendor dialog
# had genuinely closed.
#
# Scoping by "a div[@role='dialog'] that CONTAINS a descendant with
# this title text" (`.//*[...]`) is ALSO wrong — confirmed live
# (2026-11-18) via document.evaluate against the live DOM: 76 of 77
# matches resolved to the OUTER Cost Adds dialog, only 1 to the real
# vendor dialog. The vendor popup is rendered NESTED inside the Cost
# Adds dialog's own subtree (confirmed separately via
# document.activeElement's ancestor chain: kendo-dialog → ... →
# td.vendor-col → tr → tbody) — not a document-level sibling as
# originally assumed. Since the vendor dialog's title is a DESCENDANT
# of the outer Cost Adds div too, `.//*` matches BOTH; `.first` then
# grabbed one of the Cost Adds-scoped inputs instead, which is why
# "Jensen" kept landing in the Cost Adds grid's own Sort By combobox.
#
# Fix: require the title span to be this dialog's own DIRECT titlebar
# child (kendo-dialog-titlebar/span, not .//*) — true only for the
# dialog whose OWN immediate title is "Search or Add Vendor", not an
# ancestor that merely contains it somewhere deeper.
VENDOR_DIALOG_XPATH = (
    "xpath=//div[@role='dialog']"
    "[kendo-dialog-titlebar/span[contains(@class,'k-dialog-title') and normalize-space(text())='Search or Add Vendor']]"
)
# Confirmed live (2026-11-18): the input has NO type attribute at all
# (not type='text') — it's a bare <input class="k-input-inner"> inside
# a <kendo-textbox>, same input-inner pattern as every other Kendo
# control in this app.
VENDOR_SEARCH_INPUT_SELECTOR = VENDOR_DIALOG_XPATH + "//input[contains(@class,'k-input-inner')]"
# Confirmed live (2026-11-18): each result is a bare <b>VENDOR NAME</b>
# inside <div class="row mb-2"> — no dedicated result-row class exists.
# The div is the clickable unit; find_all_text on it reads the <b>'s
# text (its only content).
VENDOR_RESULT_ROW_SELECTOR = VENDOR_DIALOG_XPATH + "//div[contains(@class,'row') and contains(@class,'mb-2')]"
VENDOR_DIALOG_CLOSE_SELECTOR = VENDOR_DIALOG_XPATH + "//button[@title='Close']"
# The dialog's own backdrop is a PRECEDING SIBLING of its role='dialog'
# div, both children of the same <kendo-dialog> wrapper (confirmed live
# 2026-11-18). Clicking a vendor result closes the dialog itself
# quickly, but that backdrop's closing animation can still be mid-
# "appear" transition afterward — confirmed live: Save got blocked for
# 30s by exactly this element (tagged with the VENDOR dialog's own
# instance id, not the outer Cost Adds dialog's) even though the
# dialog had already visibly closed and no warning was ever logged.
# Must explicitly wait for this to clear, not just the dialog.
VENDOR_DIALOG_OVERLAY_SELECTOR = (
    "xpath=(" + VENDOR_DIALOG_XPATH[len("xpath="):] + ")/preceding-sibling::div[contains(@class,'k-overlay')]"
)
# The Cost Adds dialog's own container, scoped by its OWN direct
# titlebar child (title "Cost Adds") the same way VENDOR_DIALOG_XPATH
# is scoped. An earlier approach tried clicking the in-dialog Save
# button directly and waiting for this dialog's own backdrop overlay
# to clear first — abandoned (2026-11-18) once console confirmed the
# ONLY ".save-btn" on the page is the PAGE's own top-toolbar Save, not
# the dialog's, and a genuine human click on Save always persisted
# (toast: "Inventory Successfully Saved") while no automated click on
# it ever did. See SAVE_CONFIRMATION_YES_SELECTOR for the real path.
COST_ADDS_DIALOG_XPATH = (
    "xpath=//div[@role='dialog']"
    "[kendo-dialog-titlebar/span[contains(@class,'k-dialog-title') and normalize-space(text())='Cost Adds']]"
)
# Scoped Close (X) button for the Cost Adds dialog specifically —
# NOT the generic "button[title='Close']" VerifyCostAddTool uses
# (that one is only safe there because no other dialog is expected to
# be open at verification time).
COST_ADDS_CLOSE_SELECTOR = COST_ADDS_DIALOG_XPATH + "//button[@title='Close']"
# Confirmed live (2026-11-18), per user: closing the Cost Adds dialog
# with unsaved changes pops this confirmation — a FRESH top-level
# dialog, not fighting the Cost Adds dialog's own backdrop the way the
# in-dialog Save button was. Clicking "Yes" here is the reliable save
# path; clicking the in-dialog Save button directly is what's been
# intermittently/silently failing this whole time. Title text sits in
# a nested <div> inside the titlebar span, not directly on the span —
# same direct-titlebar-child scoping as COST_ADDS_DIALOG_XPATH/
# VENDOR_DIALOG_XPATH, just one level deeper.
SAVE_CONFIRMATION_DIALOG_XPATH = (
    "xpath=//div[@role='dialog']"
    "[kendo-dialog-titlebar/span[contains(@class,'k-dialog-title')]"
    "//div[normalize-space(text())='Save Confirmation']]"
)
SAVE_CONFIRMATION_YES_SELECTOR = SAVE_CONFIRMATION_DIALOG_XPATH + " >> button:has-text('Yes')"
# The REAL in-dialog Save button — confirmed live (2026-11-18) via a
# full button dump of the Cost Adds dialog: it has NO "save-btn" class
# at all (that belongs exclusively to the page's own top-toolbar Save,
# confirmed separately — only one ".save-btn" exists on the whole page
# and it's "not inside a dialog"). It's simply the last plain-text
# "Save" button inside the dialog, next to "Create Bill". Clicked as a
# primary attempt before the close-and-confirm fallback below — now
# that it's correctly scoped, a real force_click on it may well be
# reliable on its own; closing afterward is harmless either way (clean
# close if Save already took effect, "unsaved changes" prompt to catch
# it if not).
COST_ADDS_SAVE_BUTTON_SELECTOR = COST_ADDS_DIALOG_XPATH + "//button[normalize-space(.)='Save']"
NEW_ROW_MEMO_SELECTOR = LAST_ROW_XPATH + "//input[@formcontrolname='memo']"
# All rows' Memo cells (existing entries, unlike NEW_ROW_MEMO_SELECTOR
# which only targets the blank last row) — read before adding anything,
# per the existing skill's duplicate-invoice rule. Must be read with
# find_all_values, NOT find_all_text: this is an <input>, so its value
# lives in the `value` property, not as visible text — find_all_text's
# inner-text read returns "" here regardless of selector.
EXISTING_MEMO_SELECTOR = "input[formcontrolname='memo']"

MAX_ADD_NEW_ATTEMPTS = 3
# Module-level, not a default arg, so tests can monkeypatch it to 0.
SAVE_SETTLE_DELAY_SECONDS = 1.5


def _normalize_memo(text: str) -> str:
    return text.replace(" ", "")


def _read_vendor_results_with_retry(
    driver: BrowserDriver, before_count: int, attempts: int = 12, delay_ms: int = 700
) -> list[str]:
    """Same bug class as SearchInvoiceTool's dropdown race: reading the
    result list right after fill() can catch it before the filtered
    results actually land. Confirmed live (2026-11-18) that this search
    isn't instant client-side filtering — it visibly takes a few real
    seconds — so a short ~2s retry window wasn't enough; ~8s total gives
    it a real chance. Retry until the count actually changes from its
    pre-filter baseline, not just until it's non-empty (the unfiltered
    list is non-empty too)."""
    results = driver.find_all_text(VENDOR_RESULT_ROW_SELECTOR)
    for attempt in range(attempts):
        if len(results) != before_count:
            return results
        if attempt < attempts - 1:
            time.sleep(delay_ms / 1000)
            results = driver.find_all_text(VENDOR_RESULT_ROW_SELECTOR)
    return results


def _find_exact_match(results: list[str], vendor_name: str) -> str | None:
    """Case-insensitive exact match — the dialog does prefix/substring
    matching (confirmed live 2026-11-18: searching "je" returned JENSEN
    TIRE & AUTO, JET POWERTRAIN, and JERRY'S CHEVROLET), so multiple
    results is normal and not itself ambiguous; only "no result matches
    the full name exactly" is."""
    target = vendor_name.strip().casefold()
    for r in results:
        if r.strip().casefold() == target:
            return r
    return None


def _nth_xpath(selector: str, index: int) -> str:
    """1-based index into an XPath locator's matches — avoids embedding
    vendor-name text (which may contain quotes, e.g. "O'Reilly") into an
    XPath string literal just to target one specific result row."""
    assert selector.startswith("xpath=")
    return f"xpath=({selector[len('xpath='):]})[{index + 1}]"


def _is_blank_amount(raw: str) -> bool:
    """True for "", "$0.00", "0", "0.00", etc. — anything that isn't a
    real nonzero amount. Used by the blank-row guard; unparseable input
    is treated as NOT blank (fail closed, per "if a single field has
    some value then we don't update it")."""
    digits = re.sub(r"[^0-9.]", "", raw)
    if digits in ("", "0", "0.0", "0.00"):
        return True
    try:
        return float(digits) == 0.0
    except ValueError:
        return False


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
        auto_save: bool = True,
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

        # Always click Add New (with verification) before filling,
        # regardless of skip_duplicate_check — confirmed live
        # (2026-11-18) that "there's always a default blank row at the
        # end" does NOT reliably hold: without this, the blank-row
        # guard below caught the last memo-input row actually being a
        # real, pre-existing invoice, not blank. Verify the click
        # actually landed a new row (confirmed live 2026-11-18 that a
        # single click can silently no-op) and retry rather than
        # proceeding blind.
        self._click_add_new_and_verify(driver)

        # Guard against writing into a row that already has data — relying
        # on "(//tr)[last()]" always being the fresh blank row is only as
        # good as that assumption holding. Check every field we can read
        # cheaply (Memo, Cost, Vendor link) — if ANY of them already has
        # a value, stop instead of risking a write into existing data.
        # Category/Description aren't included here since their current
        # selection isn't reliably readable without opening the dropdown.
        row_memo = driver.find_all_values(NEW_ROW_MEMO_SELECTOR)
        row_cost = driver.find_all_values(NEW_ROW_COST_SELECTOR)
        row_vendor_links = driver.find_all_text(NEW_ROW_VENDOR_LINK_SELECTOR)
        memo_filled = any(v.strip() for v in row_memo)
        cost_filled = any(not _is_blank_amount(v) for v in row_cost)
        vendor_filled = any(v.strip() for v in row_vendor_links)
        if memo_filled or cost_filled or vendor_filled:
            raise StepFailure(
                self.name,
                "expected a blank new row but found existing data: "
                f"memo={row_memo!r} cost={row_cost!r} vendor_linked={row_vendor_links!r}",
            )

        # Cost, Category, and Memo are the fields this entry must have —
        # any failure here is a real StepFailure. Description has no
        # formcontrolname of its own (every existing row shows it
        # mirroring Category, confirmed live), so setting Category alone
        # is enough. Vendor is best-effort below: logged and skipped on
        # failure rather than aborting the whole entry, per instruction.
        driver.select_option(NEW_ROW_CATEGORY_SELECTOR, category)
        driver.fill(NEW_ROW_COST_SELECTOR, f"{cost:.2f}")
        # type_text, not fill — confirmed live (2026-11-18) that fill()
        # corrupted this masked Kendo date input ("11/18/0006" instead of
        # "11/18/2026"), since direct value assignment bypasses its mask.
        driver.type_text(NEW_ROW_DATE_SELECTOR, invoice_date.strftime("%m/%d/%Y"))

        vendor_warning = self._select_vendor(driver, vendor_name)

        # type_text, not fill — confirmed live (2026-11-18) that fill()
        # left Memo visually correct but not actually saved: after a
        # refresh, Category/Cost/Vendor/Date all persisted but Memo was
        # blank. Same bug class as the masked date field and the vendor
        # search box — direct value assignment doesn't reach the
        # underlying reactive form control the way real keystrokes do.
        driver.type_text(NEW_ROW_MEMO_SELECTOR, memo)

        data = {"category": category, "vendor": vendor_name}
        if vendor_warning:
            data["warnings"] = [vendor_warning]
            print(f"[{self.name}] WARNING: {vendor_warning}")

        if not auto_save:
            # Debugging aid only (2026-11-18): every field is filled but
            # Save is deliberately NOT clicked, so a human can click it
            # themselves and inspect exactly what does/doesn't persist —
            # isolates the row-filling logic from the intermittent
            # silent-save-failure this flag exists to help diagnose.
            data["auto_save"] = False
            return ToolResult(status=ToolStatus.SUCCESS, data=data)

        time.sleep(SAVE_SETTLE_DELAY_SECONDS)
        # Primary: click the real, correctly-scoped in-dialog Save
        # button (confirmed live 2026-11-18 via a full button dump —
        # the earlier SAVE_BUTTON_SELECTOR guess was clicking the
        # PAGE's own top-toolbar Save button instead, not this one).
        driver.force_click(COST_ADDS_SAVE_BUTTON_SELECTOR)
        time.sleep(SAVE_SETTLE_DELAY_SECONDS)
        # Fallback/confirmation: close the dialog regardless. If Save
        # already took effect this is a clean close (no prompt). If it
        # silently didn't, closing with unsaved changes pops a fresh
        # "Save Confirmation" dialog — confirmed live to be a reliable
        # save path on its own — and clicking "Yes" catches it.
        #
        # force_click, not click — confirmed live (2026-11-18): right
        # after a real Save click, the grid re-renders (the save
        # actually took effect), and that ongoing re-render kept the
        # Close button's bounding box from ever settling long enough
        # for Playwright's plain click() stability wait, hanging 30s
        # with no actual blocking overlay involved this time.
        #
        # Only attempt this if the dialog is STILL open — confirmed
        # live (2026-11-18) that the Save click can close the dialog
        # on its own when it succeeds, in which case Close has nothing
        # left to click and waiting on it hangs for the full timeout.
        if driver.wait_visible(MODAL_OPEN_SELECTOR, timeout_ms=3000):
            driver.force_click(COST_ADDS_CLOSE_SELECTOR)
            if driver.wait_visible(SAVE_CONFIRMATION_YES_SELECTOR, timeout_ms=5000):
                driver.force_click(SAVE_CONFIRMATION_YES_SELECTOR)
        return ToolResult(status=ToolStatus.SUCCESS, data=data)

    def _click_add_new_and_verify(self, driver: BrowserDriver) -> None:
        """Click Add New and confirm the row count actually went up —
        confirmed live (2026-11-18) that trusting the click alone isn't
        enough; retries a few times before giving up rather than
        silently filling whatever row happened to be last already."""
        before = len(driver.find_all_text(ROW_SELECTOR))
        for _ in range(MAX_ADD_NEW_ATTEMPTS):
            driver.click(MODAL_OPEN_SELECTOR)
            time.sleep(0.5)
            after = len(driver.find_all_text(ROW_SELECTOR))
            if after > before:
                return
            before = after
        raise StepFailure(
            self.name, f"Add New did not append a new row after {MAX_ADD_NEW_ATTEMPTS} attempts"
        )

    def _select_vendor(self, driver: BrowserDriver, vendor_name: str) -> str | None:
        """Best-effort: Cost/Category/Memo are the required fields, Vendor
        is not — on any failure here, log a warning and leave the row's
        Vendor unset rather than aborting the whole entry. Still never
        invents a vendor match; it only picks whichever candidate is an
        EXACT (case-insensitive) match for vendor_name.

        Search strategy confirmed live (2026-11-18): the dialog does
        prefix/substring matching, not a full-string lookup, so
        searching the full vendor_name can return zero hits even when
        the vendor exists. Try the full name first; if that comes back
        empty, retry with just the first word (same as before), then
        pick the exact match out of whichever candidate set came back —
        multiple candidates is normal here, not itself a reason to skip.

        Every early-return path below MUST leave the Cost Adds dialog in
        a clickable state — confirmed live (2026-11-18) that abandoning
        the "Search or Add Vendor" popup without closing it leaves a
        k-overlay backdrop that blocks every subsequent click (including
        Save) for the rest of the run. Also confirmed live: a click on
        the result row can silently fail to register as a selection —
        verify the dialog actually closed before trusting it worked."""
        if not vendor_name:
            return "no vendor name given — Vendor left unset"

        try:
            driver.click(NEW_ROW_VENDOR_SEARCH_ICON_SELECTOR)
            if not driver.wait_visible(VENDOR_SEARCH_INPUT_SELECTOR, timeout_ms=5000):
                self._close_vendor_dialog(driver)
                return "vendor search box did not open — Vendor left unset"

            results = self._search_vendor_candidates(driver, vendor_name)
            match = _find_exact_match(results, vendor_name)
            if not match and vendor_name.split()[0] != vendor_name:
                results = self._search_vendor_candidates(driver, vendor_name.split()[0])
                match = _find_exact_match(results, vendor_name)

            if not match:
                self._close_vendor_dialog(driver)
                return (
                    f"no result matched {vendor_name!r} exactly among "
                    f"{len(results)} candidates — Vendor left unset (never guessing a close name)"
                )

            driver.click(_nth_xpath(VENDOR_RESULT_ROW_SELECTOR, results.index(match)))
            if driver.wait_visible(VENDOR_SEARCH_INPUT_SELECTOR, timeout_ms=2000):
                # Dialog is still open — the click didn't register as a
                # real selection. Confirmed live (2026-11-18): this can
                # happen silently with no exception at all.
                self._close_vendor_dialog(driver)
                return f"selecting {match!r} did not close the vendor dialog — Vendor left unset"
            # The dialog itself closed, but confirmed live (2026-11-18)
            # that its backdrop's closing animation can still be
            # mid-transition — wait for it specifically before handing
            # control back, or the next click (Memo fill, Save) can get
            # blocked by a leftover overlay even though nothing here
            # failed. Best-effort: don't abort the entry over a slow
            # animation, just give it a real chance to finish.
            driver.wait_hidden(VENDOR_DIALOG_OVERLAY_SELECTOR, timeout_ms=15000)
            return None
        except Exception as exc:
            self._close_vendor_dialog(driver)
            return f"vendor selection failed ({exc}) — Vendor left unset"

    def _search_vendor_candidates(self, driver: BrowserDriver, query: str) -> list[str]:
        # type_text, not fill — confirmed live (2026-11-18) that fill()
        # sets the DOM value directly without firing the real keystroke
        # events this search's debounce listens for, so typing "jen" by
        # hand showed a loading spinner and got results, but fill()ing
        # the same text triggered no search at all (list stayed
        # unfiltered, 1-98 of 98).
        before_count = len(driver.find_all_text(VENDOR_RESULT_ROW_SELECTOR))
        driver.type_text(VENDOR_SEARCH_INPUT_SELECTOR, query)
        return _read_vendor_results_with_retry(driver, before_count)

    def _close_vendor_dialog(self, driver: BrowserDriver) -> None:
        """Deliberately NOT driver.dismiss_blocking_modals() — that picks
        the FIRST [role='dialog'] on the page, which is the outer Cost
        Adds dialog (it also has role="dialog"), and would close the
        whole thing instead of just this nested popup. Target the LAST
        (innermost/topmost) dialog's own Close button instead. Best
        effort — swallow failures here, we're already on a failure path
        and must not let a close-button miss mask the real warning."""
        try:
            driver.click(VENDOR_DIALOG_CLOSE_SELECTOR)
        except Exception:
            pass
