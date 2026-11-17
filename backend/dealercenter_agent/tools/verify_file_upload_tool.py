from ..automation.base import BrowserDriver
from .base import Tool, ToolResult, ToolStatus
from .file_upload_tool import EXISTING_FILE_NAME_SELECTOR


class VerifyFileUploadTool(Tool):
    """Re-reads the Files grid after a save and confirms the uploaded PDF
    is actually listed. A FAILED result here is a normal, expected
    outcome — not a raised StepFailure — exactly like VerifyTool in the
    Phase 1 plan: it's the signal the caller uses to decide `created` vs.
    handing off to the recovery layer, never a thing this Tool itself
    needs to recover from. "PDF not listed on Files" is the exact string
    the existing skill's callback contract requires."""

    name = "VerifyFileUploadTool"

    def run(self, driver: BrowserDriver, file_name: str, **kwargs) -> ToolResult:
        existing_names = driver.find_all_text(EXISTING_FILE_NAME_SELECTOR)
        if any(file_name in name for name in existing_names):
            return ToolResult(status=ToolStatus.SUCCESS, data={"file_name": file_name})
        return ToolResult(status=ToolStatus.FAILED, reason="PDF not listed on Files")
