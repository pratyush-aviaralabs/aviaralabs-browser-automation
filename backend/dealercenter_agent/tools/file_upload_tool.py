from pathlib import Path

from ..automation.base import BrowserDriver
from .base import StepFailure, Tool, ToolResult, ToolStatus

MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024  # DealerCenter's own stated upload limit

# FILES_TAB_SELECTOR and EXISTING_FILE_NAME_SELECTOR confirmed against
# the real DOM via the live Inspector (2026-11-17): the tab trigger is a
# plain <span class="k-link">Files</span>, and each row's filename is
# the text of an <a> inside <span class="file-name">, not a
# [data-field='FileName'] grid cell as originally guessed.
FILES_TAB_SELECTOR = ".k-link:has-text('Files')"
EXISTING_FILE_NAME_SELECTOR = ".file-name a"
# Still unverified placeholders — the Inspector hasn't been clicked
# through an actual upload yet (Browse, filename field, category,
# top Save).
FILE_INPUT_SELECTOR = "input[type='file']"
FILE_NAME_INPUT_SELECTOR = "input[name='fileName']"
CATEGORY_DROPDOWN_SELECTOR = "[data-field='Category']"
# Exact text match, not :has-text() substring — "Save and Close" sits
# right next to "Save" on this page and a substring match would risk
# clicking the wrong one via .first.
TOP_SAVE_BUTTON_SELECTOR = "button:text-is('Save')"


class FileUploadTool(Tool):
    """Uploads local_pdf_path into the Files tab of the vehicle detail
    page SearchInvoiceTool lands on, matching the existing
    dealercenter-cost-adds skill's exact rules: skip the upload entirely
    if the invoice number is already in an existing File Name (no second
    copy), 25MB limit enforced locally before any browser call, and the
    page-level top Save — never "Save and Close", never "Create Bill"."""

    name = "FileUploadTool"

    def run(
        self,
        driver: BrowserDriver,
        local_pdf_path: str,
        file_name: str,
        invoice_number: str,
        category: str = "Others",
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
            return ToolResult(status=ToolStatus.EXISTS, data={"file_name": file_name})

        driver.upload_file(FILE_INPUT_SELECTOR, str(path))
        driver.fill(FILE_NAME_INPUT_SELECTOR, file_name)
        driver.select_option(CATEGORY_DROPDOWN_SELECTOR, category)
        driver.click(TOP_SAVE_BUTTON_SELECTOR)

        return ToolResult(status=ToolStatus.SUCCESS, data={"file_name": file_name, "category": category})
