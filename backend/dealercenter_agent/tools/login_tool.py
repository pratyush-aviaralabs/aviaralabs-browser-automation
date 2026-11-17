from typing import Callable

from ..automation.base import BrowserDriver
from .base import StepFailure, Tool, ToolResult, ToolStatus

BASE_URL = "https://app.dealercenter.net"
AUTH_HOST = "auth.dealercenter.net"
USERNAME_SELECTOR = "#username"
PASSWORD_SELECTOR = "#password"
SEARCH_BOX_SELECTOR = "input[type='search'], input[placeholder*='Search' i]"
MFA_CODE_SELECTOR = (
    "input[name='code'], input[autocomplete='one-time-code'], "
    "input[inputmode='numeric'], input[type='tel']"
)
MFA_MAX_ATTEMPTS = 2  # initial attempt + 1 retry, matching the POC's behavior


class LoginTool(Tool):
    """Ported from the Playwright POC's proven do_login/handle_mfa control
    flow, with one structural change: it raises StepFailure instead of
    printing and returning False, since that exception is how the
    recovery layer finds out something needs a decision. Session reuse
    (storage_state) is a driver-construction concern, not this Tool's —
    see create_playwright_driver; by the time this runs, `driver` may
    already be carrying a valid, authenticated session."""

    name = "LoginTool"

    def run(
        self,
        driver: BrowserDriver,
        username: str,
        password: str,
        mfa_code_provider: Callable[[], str],
        **kwargs,
    ) -> ToolResult:
        driver.goto(BASE_URL)

        if self._looks_logged_in(driver):
            driver.dismiss_blocking_modals()
            return ToolResult(status=ToolStatus.SUCCESS, data={"reused_session": True})

        if not driver.wait_visible(USERNAME_SELECTOR, timeout_ms=15000):
            raise StepFailure(self.name, "username field not found on login page")

        driver.fill(USERNAME_SELECTOR, username)
        driver.fill(PASSWORD_SELECTOR, password)
        driver.click_submit_near(PASSWORD_SELECTOR)

        if self._looks_like_mfa(driver):
            self._handle_mfa(driver, mfa_code_provider)

        if not self._looks_logged_in(driver):
            raise StepFailure(
                self.name, f"login did not succeed, url={driver.current_url()}"
            )

        driver.dismiss_blocking_modals()
        return ToolResult(status=ToolStatus.SUCCESS, data={"reused_session": False})

    def _looks_logged_in(self, driver: BrowserDriver) -> bool:
        if AUTH_HOST in driver.current_url():
            return False
        # Being off the auth host is the real proof login worked — the
        # search-box wait is a bonus confirmation, never a hard gate that
        # can override a real login into a false "failed" (see Review
        # Focus in the Phase 1 plan: this exact bug cost a saved session
        # once already in the POC).
        driver.wait_visible(SEARCH_BOX_SELECTOR, timeout_ms=15000)
        return True

    def _looks_like_mfa(self, driver: BrowserDriver) -> bool:
        if AUTH_HOST in driver.current_url():
            return not driver.wait_visible(PASSWORD_SELECTOR, timeout_ms=500)
        return driver.wait_visible(MFA_CODE_SELECTOR, timeout_ms=1000)

    def _handle_mfa(self, driver: BrowserDriver, mfa_code_provider: Callable[[], str]) -> None:
        if not driver.wait_visible(MFA_CODE_SELECTOR, timeout_ms=10000):
            raise StepFailure(self.name, "MFA code field not found")

        for _ in range(MFA_MAX_ATTEMPTS):
            code = mfa_code_provider()
            if not (code.isdigit() and len(code) == 6):
                raise StepFailure(self.name, f"invalid MFA code format: {code!r}")
            driver.fill(MFA_CODE_SELECTOR, code)
            driver.click_submit_near(MFA_CODE_SELECTOR)
            if not self._looks_like_mfa(driver):
                return

        raise StepFailure(self.name, "MFA code rejected after retries")
