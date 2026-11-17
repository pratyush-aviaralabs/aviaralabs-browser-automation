from ..automation.base import BrowserDriver
from .base import Tool, ToolResult, ToolStatus

# Same keyword set the existing dealercenter-cost-adds skill used to
# decide Repair vs. Parts — reused here for the binary Repair/Other call.
REPAIR_KEYWORDS = (
    "labor",
    "lube",
    "oil-change service",
    "oil change",
    "rotation",
    "inspection",
    "install",
    "diagnostic",
)


class ClassifyCategoryTool(Tool):
    """Pure data classification — no browser interaction, so `driver` is
    accepted but unused, purely to keep the uniform Tool.run(driver, **kwargs)
    signature a WorkflowStep can call generically regardless of which tool
    a step names."""

    name = "ClassifyCategoryTool"

    def run(self, driver: BrowserDriver | None, line_items: list[str], **kwargs) -> ToolResult:
        category = "Repair" if self._is_repair(line_items) else "Other"
        return ToolResult(status=ToolStatus.SUCCESS, data={"category": category})

    def _is_repair(self, line_items: list[str]) -> bool:
        return any(
            keyword in item.lower() for item in line_items for keyword in REPAIR_KEYWORDS
        )
