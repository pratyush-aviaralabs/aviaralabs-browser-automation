import time
from contextlib import contextmanager
from typing import Iterator

from playwright.sync_api import Page, sync_playwright

from .base import BrowserDriver

MODAL_CLOSE_SELECTORS = (
    "[role='dialog'] button[aria-label='Close'], "
    "[role='dialog'] button:has-text('Got it'), "
    "[role='dialog'] button:has-text('Dismiss'), "
    "[role='dialog'] button:has-text('OK'), "
    "[role='dialog'] .close, [role='dialog'] [class*='close' i], "
    ".k-dialog-close, .k-dialog-titlebar-actions button, "
    ".k-window-titlebar .k-button, button[aria-label='Close'], "
    "button[title='Close']"
)
MODAL_CONTAINER_SELECTOR = "[role='dialog'], .k-dialog, .k-window"
TRUST_BROWSER_BUTTON_SELECTOR = "button:has-text('trust this browser')"


class PlaywrightDriver(BrowserDriver):
    """Thin wrapper around a Playwright Page. All the DOM-handling logic
    here (duplicate aria-hidden submit buttons, async Kendo modals) was
    proven live against DealerCenter in the POC script before being
    ported here — see tests/automation/test_playwright_driver.py for the
    fixture-based regression coverage of both bugs."""

    def __init__(self, page: Page):
        self.page = page

    def _locator(self, selector: str):
        """Resolves a selector to a Playwright Locator, transparently
        supporting a reserved "iframe=<frame_selector> >> <inner_selector>"
        convention for content inside an <iframe> — confirmed live
        (2026-11-18) that DealerCenter renders a Deal's own contract
        view (where the vehicle quick-link lives) inside an <iframe
        id="dc-iframe-dialog">, which a plain page.locator() can never
        reach regardless of selector correctness; Playwright requires
        an explicit frame_locator() for that. Every other method here
        routes through this instead of calling self.page.locator()
        directly, so any selector in the codebase can opt into
        targeting iframe content with the same convention."""
        if selector.startswith("iframe="):
            frame_selector, inner_selector = selector[len("iframe="):].split(" >> ", 1)
            return self.page.frame_locator(frame_selector).locator(inner_selector)
        return self.page.locator(selector)

    def goto(self, url: str) -> None:
        self.page.goto(url, wait_until="domcontentloaded")

    def current_url(self) -> str:
        return self.page.url

    def fill(self, selector: str, value: str) -> None:
        self._locator(selector).first.fill(value)

    def type_text(self, selector: str, text: str) -> None:
        locator = self._locator(selector).first
        locator.click()
        locator.press("Control+A")
        locator.press_sequentially(text)

    def click(self, selector: str) -> None:
        self._locator(selector).first.click()

    def force_click(self, selector: str) -> None:
        # click(force=True), NOT evaluate("el => el.click()") — confirmed
        # live (2026-11-18): the DOM .click() method fires an UNTRUSTED
        # synthetic event (event.isTrusted === false). No automated run
        # using it ever showed the app's own "Inventory Successfully
        # Saved" toast, while a genuine human click always did and
        # always persisted — strong evidence the real save handler
        # requires a trusted event. click(force=True) still dispatches a
        # real CDP-level mouse click (isTrusted: true, same mechanism a
        # human click uses) at the element's coordinates; it only skips
        # Playwright's own pre-click actionability wait. Caller is
        # responsible for waiting out any real blocking overlay first
        # (see CostAddTool's wait_hidden on the dialog's own backdrop)
        # rather than relying on this to skip past a genuine blocker.
        self._locator(selector).first.click(force=True)

    def click_submit_near(self, field_selector: str) -> None:
        field = self.page.locator(field_selector).first
        btn = field.locator(
            "xpath=ancestor::form[1]//button[@type='submit'][not(@aria-hidden='true')]"
        ).last
        btn.wait_for(timeout=10000)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if btn.is_enabled():
                btn.click()
                return
            time.sleep(0.2)
        btn.click()

    def wait_visible(self, selector: str, timeout_ms: int = 2000) -> bool:
        try:
            self._locator(selector).first.wait_for(state="visible", timeout=timeout_ms)
            return True
        except Exception:
            return False

    def wait_hidden(self, selector: str, timeout_ms: int = 2000) -> bool:
        try:
            self._locator(selector).first.wait_for(state="hidden", timeout=timeout_ms)
            return True
        except Exception:
            return False

    def dismiss_blocking_modals(self, max_rounds: int = 5) -> None:
        for _ in range(max_rounds):
            trust_btn = self.page.locator(TRUST_BROWSER_BUTTON_SELECTOR).first
            if self.wait_visible(TRUST_BROWSER_BUTTON_SELECTOR, 2500):
                trust_btn.click()
                self.page.wait_for_timeout(1000)
                continue

            modal = self.page.locator(MODAL_CONTAINER_SELECTOR).first
            if not self.wait_visible(MODAL_CONTAINER_SELECTOR, 2500):
                break

            closed = False
            close_btn = self.page.locator(MODAL_CLOSE_SELECTORS).first
            if self.wait_visible(MODAL_CLOSE_SELECTORS, 1500):
                try:
                    close_btn.click(timeout=2000)
                    closed = True
                except Exception:
                    pass

            if not closed:
                self.page.keyboard.press("Escape")

            self.page.wait_for_timeout(800)

        try:
            self.page.locator(".k-overlay").first.wait_for(state="hidden", timeout=5000)
        except Exception:
            pass

    def select_option(self, trigger_selector: str, option_text: str) -> None:
        self._locator(trigger_selector).first.click()
        self.page.get_by_text(option_text, exact=True).first.click()

    def upload_via_button(self, trigger_selector: str, file_path: str) -> None:
        with self.page.expect_file_chooser() as fc_info:
            self._locator(trigger_selector).first.click()
        fc_info.value.set_files(file_path)

    def get_text(self, selector: str) -> str:
        return self._locator(selector).first.inner_text()

    def find_all_text(self, selector: str) -> list[str]:
        return self._locator(selector).all_inner_texts()

    def find_all_values(self, selector: str) -> list[str]:
        return self._locator(selector).evaluate_all("els => els.map(el => el.value)")

    def save_session(self) -> dict:
        return self.page.context.storage_state()

    def screenshot(self, path: str) -> None:
        self.page.screenshot(path=path)


@contextmanager
def create_playwright_driver(
    headless: bool = True, storage_state: dict | None = None
) -> Iterator[PlaywrightDriver]:
    """The construction point where a saved session actually gets applied
    — Playwright only accepts storage_state when the context is created,
    so this is the one place that can honor it. Usage:

        with create_playwright_driver(headless=True, storage_state=saved) as driver:
            ... use driver ...
    """
    # Fixed to match the web UI's screencast maxWidth/maxHeight (see
    # web/runner.py) — the Inspector maps a click's pixel position on the
    # JPEG frame directly onto real page coordinates, which only lines up
    # if the frame size and the actual viewport size are the same.
    viewport = {"width": 1280, "height": 800}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = (
            browser.new_context(storage_state=storage_state, viewport=viewport)
            if storage_state
            else browser.new_context(viewport=viewport)
        )
        page = context.new_page()
        try:
            yield PlaywrightDriver(page)
        finally:
            browser.close()
