import time
from decimal import Decimal

from ..automation.base import BrowserDriver
from .base import StepFailure, Tool, ToolResult, ToolStatus
from .cost_add_tool import (
    COST_ADDS_LINK_SELECTOR,
    EXISTING_MEMO_SELECTOR,
    MODAL_OPEN_SELECTOR,
    _normalize_memo,
)

# Every row's Cost is a <kendo-numerictextbox formcontrolname="amount">
# wrapping a plain <input> with no formcontrolname of its own (confirmed
# live 2026-11-18, same structure EXISTING_MEMO_SELECTOR's sibling field
# uses) — read across ALL rows, same document order as
# EXISTING_MEMO_SELECTOR, so the two lists line up index-for-index.
ALL_COST_SELECTOR = "xpath=//kendo-numerictextbox[@formcontrolname='amount']//input"
# The Cost Adds dialog's own titlebar Close (X) button — confirmed live
# (2026-11-18) via the same dialog dump that confirmed the vendor
# dialog's structure.
COST_ADDS_CLOSE_SELECTOR = "button[title='Close']"

MAX_VERIFY_ATTEMPTS = 5
VERIFY_RETRY_DELAY_SECONDS = 2


def _parse_amount(raw: str) -> Decimal | None:
    digits = "".join(ch for ch in raw if ch.isdigit() or ch == ".")
    if not digits:
        return None
    try:
        return Decimal(digits)
    except Exception:
        return None


class VerifyCostAddTool(Tool):
    """Reopens the Cost Adds modal after a Save and independently
    confirms a row with the given memo (invoice number) actually exists
    with the expected Cost — doesn't trust CostAddTool's own SUCCESS
    report. Confirmed live (2026-11-18) that a reported SUCCESS save can
    still silently drop a field: one run persisted Category/Cost/Vendor/
    Date correctly but Memo came back blank after a refresh. A FAILED
    result here is a normal, expected outcome, not a raised StepFailure
    — same contract as VerifyFileUploadTool.

    Retries the whole close-and-reopen cycle a few times before giving
    up — confirmed live (2026-11-18) that reopening immediately after
    Save can still show pre-save grid data (manually waiting a moment
    before reopening found the row fine; this script's instant reopen
    didn't), suggesting Save kicks off an async persist this verify
    step can otherwise race."""

    name = "VerifyCostAddTool"

    def run(self, driver: BrowserDriver, memo: str, expected_cost: Decimal, **kwargs) -> ToolResult:
        target = _normalize_memo(memo)
        memos: list[str] = []
        for attempt in range(MAX_VERIFY_ATTEMPTS):
            driver.click(COST_ADDS_LINK_SELECTOR)
            if not driver.wait_visible(MODAL_OPEN_SELECTOR, timeout_ms=10000):
                raise StepFailure(self.name, "Cost Adds modal did not open")

            memos = driver.find_all_values(EXISTING_MEMO_SELECTOR)
            if any(_normalize_memo(m) == target for m in memos):
                break
            if attempt < MAX_VERIFY_ATTEMPTS - 1:
                driver.click(COST_ADDS_CLOSE_SELECTOR)
                time.sleep(VERIFY_RETRY_DELAY_SECONDS)

        matches = [i for i, m in enumerate(memos) if _normalize_memo(m) == target]
        if not matches:
            return ToolResult(
                status=ToolStatus.FAILED,
                reason=f"memo {memo!r} not found in Cost Adds after save (retried {MAX_VERIFY_ATTEMPTS}x)",
            )
        if len(matches) > 1:
            return ToolResult(
                status=ToolStatus.FAILED,
                reason=f"memo {memo!r} appears {len(matches)} times in Cost Adds — ambiguous",
            )

        index = matches[0]
        costs = driver.find_all_values(ALL_COST_SELECTOR)
        if index >= len(costs):
            return ToolResult(
                status=ToolStatus.FAILED,
                reason=f"memo {memo!r} found but its row's Cost could not be read (row/cost count mismatch)",
            )

        actual_cost = _parse_amount(costs[index])
        if actual_cost is None or actual_cost != Decimal(expected_cost).quantize(Decimal("0.01")):
            return ToolResult(
                status=ToolStatus.FAILED,
                reason=f"memo {memo!r} found but Cost is {costs[index]!r}, expected {expected_cost}",
            )

        return ToolResult(status=ToolStatus.SUCCESS, data={"memo": memo, "cost": str(actual_cost)})
