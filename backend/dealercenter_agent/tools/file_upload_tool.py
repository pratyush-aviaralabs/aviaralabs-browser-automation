from pathlib import Path

from ..automation.base import BrowserDriver
from .base import StepFailure, Tool, ToolResult, ToolStatus

MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024  # DealerCenter's own stated upload limit

# All confirmed against the real DOM via the live Inspector (2026-11-17).
FILES_TAB_SELECTOR = ".k-link:has-text('Files')"
EXISTING_FILE_NAME_SELECTOR = ".file-name a"
# The dropzone has NO <input type="file"> sitting in the DOM at rest —
# just this button, which opens the browser's native file-chooser
# dialog (see BrowserDriver.upload_via_button). Confirmed live that
# selecting a file here uploads it IMMEDIATELY under its own original
# filename, with Category defaulting to "Others" — there is no
# intermediate "set filename/category, then click Save" step, which is
# what the first version of this tool incorrectly assumed.
BROWSE_BUTTON_SELECTOR = "button:has-text('Browse')"


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

        existing_names = driver.find_all_text(EXISTING_FILE_NAME_SELECTOR)
        if any(invoice_number in name for name in existing_names if name.strip()):
            return ToolResult(status=ToolStatus.EXISTS, data={"uploaded_as": path.stem})

        driver.upload_via_button(BROWSE_BUTTON_SELECTOR, str(path))

        if not driver.wait_visible(f"text={path.stem}", timeout_ms=15000):
            raise StepFailure(
                self.name, f"uploaded but {path.stem!r} never appeared in the Files grid"
            )

        return ToolResult(status=ToolStatus.SUCCESS, data={"uploaded_as": path.stem, "category": "Others"})
