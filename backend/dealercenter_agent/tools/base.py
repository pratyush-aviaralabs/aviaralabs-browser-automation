from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from pydantic import BaseModel

from ..automation.base import BrowserDriver


class ToolStatus(str, Enum):
    SUCCESS = "success"
    EXISTS = "exists"
    FAILED = "failed"


class ToolResult(BaseModel):
    status: ToolStatus
    data: dict[str, Any] = {}
    reason: str | None = None


class StepFailure(Exception):
    """Raised by a Tool for any state it cannot recover from on its own.
    This is the handoff point to the Phase 3 LangGraph recovery layer —
    everything the recovery agent needs to classify and (maybe) repair the
    failure travels on this exception, not in a log line somewhere."""

    def __init__(
        self,
        tool_name: str,
        reason: str,
        screenshot_path: str | None = None,
        dom_snapshot: str | None = None,
    ):
        super().__init__(f"{tool_name}: {reason}")
        self.tool_name = tool_name
        self.reason = reason
        self.screenshot_path = screenshot_path
        self.dom_snapshot = dom_snapshot


class Tool(ABC):
    name: str

    @abstractmethod
    def run(self, driver: BrowserDriver, **kwargs: Any) -> ToolResult: ...
