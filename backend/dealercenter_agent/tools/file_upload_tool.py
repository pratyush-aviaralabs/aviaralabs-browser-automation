import time
from pathlib import Path

from ..automation.base import BrowserDriver
from .base import StepFailure, Tool, ToolResult, ToolStatus

MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024  # DealerCenter's own stated upload limit
MAX_GRID_PAGES = 20  # safety cap, not an expected real page count

# All confirmed against the real DOM via the live Inspector (2026-11-17).
FILES_TAB_SELECTOR = ".k-link:has-text('Files')"
EXISTING_FILE_NAME_SELECTOR = ".file-name a"
# Unverified placeholder — confirmed live (2026-11-17) that the Files
# grid IS paginated (an invoice already existed on page 2, invisible to
# a single-page read), but the actual pager control hasn't been
# inspected yet. Needs the same live-DOM correction every other
# selector here has already gone through.
NEXT_PAGE_BUTTON_SELECTOR = ".k-pager-nav[title='Go to the next page']"
# The dropzone has NO <input type="file"> sitting in the DOM at rest —
# just this button, which opens the browser's native file-chooser
# dialog (see BrowserDriver.upload_via_button). Confirmed live that
# selecting a file here uploads it IMMEDIATELY under its own original
# filename, with Category defaulting to "Others" — there is no
# intermediate "set filename/category, then click Save" step, which is
# what the first version of this tool incorrectly assumed.
BROWSE_BUTTON_SELECTOR = "button:has-text('Browse')"
# DealerCenter's own server-side duplicate-FILENAME check (distinct from
# our invoice-number check in the grid) — fires when a file with this
# exact literal name was already uploaded. Confirmed live (2026-11-17).
DUPLICATE_FILENAME_ERROR_SELECTOR = "text=/There is already a file named/i"


def all_file_names(driver: BrowserDriver, max_pages: int = MAX_GRID_PAGES) -> list[str]:
    """Walks every page of the Files grid, collecting every row's
    filename — a single-page read is exactly what let 3 duplicate
    uploads happen (a correctly-named match sat on page 2, invisible to
    a page-1-only check). Stops on: no next-page control found, the
    click doesn't change what's rendered (last page / disabled button,
    detected by comparing page contents rather than guessing at Kendo's
    disabled-state markup), or max_pages as a hard safety cap."""
    names: list[str] = []
    seen_pages: list[list[str]] = []
    for _ in range(max_pages):
        current = driver.find_all_text(EXISTING_FILE_NAME_SELECTOR)
        names.extend(current)
        if current in seen_pages:
            break
        seen_pages.append(current)
        if not driver.wait_visible(NEXT_PAGE_BUTTON_SELECTOR, timeout_ms=1000):
            break
        try:
            driver.click(NEXT_PAGE_BUTTON_SELECTOR)
        except Exception:
            break
        driver.wait_visible(EXISTING_FILE_NAME_SELECTOR, timeout_ms=5000)
    return names


# Module-level, not bound as default args, so tests can monkeypatch
# these down (e.g. 50ms) for the "never appears" case instead of a real
# unit test burning 20 real seconds on an expected-timeout path.
UPLOAD_CONFIRM_TIMEOUT_MS = 20000
UPLOAD_CONFIRM_POLL_MS = 1500
# Confirmed live (2026-11-18): re-uploading the exact same PDF a second
# time has no invoice-number-based duplicate signal to catch it (its
# filename doesn't contain the invoice number at all) — the ONLY thing
# that should catch it is this dialog, and 3s was too short, same
# "async dialogs need more than a couple seconds" lesson hit elsewhere
# in this codebase. Missing it meant falling through to
# wait_for_file_across_pages(), which correctly reported failure since
# nothing NEW had actually been added.
DUPLICATE_ERROR_WAIT_MS = 8000


def wait_for_file_across_pages(driver: BrowserDriver, stem: str) -> bool:
    """Polls all_file_names() — not a single page — until stem shows up
    or the timeout elapses. Two things a one-page, one-shot check both
    miss: new uploads land at the END of the LAST page (this grid sorts
    oldest-first, confirmed live), and the row can take a moment to
    register after the upload call returns."""
    deadline = time.monotonic() + UPLOAD_CONFIRM_TIMEOUT_MS / 1000
    while True:
        if any(stem in name for name in all_file_names(driver)):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(UPLOAD_CONFIRM_POLL_MS / 1000)


class FileUploadTool(Tool):
    """Uploads local_pdf_path into the Files tab of the vehicle detail
    page SearchInvoiceTool lands on, matching the existing
    dealercenter-cost-adds skill's duplicate-check rule: skip the upload
    entirely if the invoice number is already in an existing File Name.
    25MB limit enforced locally before any browser call.

    Deliberately does NOT rename the uploaded file to the "vendor short
    name + invoice number" convention the original skill specifies — it
    uploads under the PDF's own original filename. Confirmed live that
    DealerCenter's upload flow has no rename step built into it; each
    row's pencil/edit icon is presumably how renaming actually happens,
    but that interaction hasn't been inspected yet. This is a known,
    flagged gap, not an oversight — a follow-up tool once that's
    confirmed."""

    name = "FileUploadTool"

    def run(
        self,
        driver: BrowserDriver,
        local_pdf_path: str,
        invoice_number: str,
        **kwargs,
    ) -> ToolResult:
        path = Path(local_pdf_path)
        if not path.exists():
            raise StepFailure(self.name, f"local_pdf_path does not exist: {local_pdf_path}")

        size = path.stat().st_size
        if size > MAX_FILE_SIZE_BYTES:
            raise StepFailure(
                self.name,
                f"{local_pdf_path} is {size} bytes, over the 25MB limit ({MAX_FILE_SIZE_BYTES})",
            )

        driver.click(FILES_TAB_SELECTOR)
        if not driver.wait_visible(EXISTING_FILE_NAME_SELECTOR, timeout_ms=10000):
            raise StepFailure(self.name, "Files tab did not load")

        existing_names = all_file_names(driver)
        if any(invoice_number in name for name in existing_names if name.strip()):
            return ToolResult(status=ToolStatus.EXISTS, data={"uploaded_as": path.stem})

        driver.upload_via_button(BROWSE_BUTTON_SELECTOR, str(path))

        # DealerCenter rejecting on a literal filename collision is a
        # real duplicate signal too, just a different one than our own
        # invoice-number check above — treat it the same way: EXISTS,
        # not a failure, after clearing the dialog it leaves behind.
        if driver.wait_visible(DUPLICATE_FILENAME_ERROR_SELECTOR, timeout_ms=DUPLICATE_ERROR_WAIT_MS):
            driver.dismiss_blocking_modals()
            return ToolResult(status=ToolStatus.EXISTS, data={"uploaded_as": path.stem})

        # Must check every page, not just whatever's currently rendered
        # — new uploads land at the end of the LAST page (oldest-first
        # sort, confirmed live), which a page-1-only check never sees.
        if not wait_for_file_across_pages(driver, path.stem):
            raise StepFailure(
                self.name,
                f"uploaded but {path.stem!r} never appeared in the Files grid (checked all pages)",
            )

        return ToolResult(status=ToolStatus.SUCCESS, data={"uploaded_as": path.stem, "category": "Others"})
