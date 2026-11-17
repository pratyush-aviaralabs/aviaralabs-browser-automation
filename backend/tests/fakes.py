"""A single, injectable FakeBrowserDriver reused across tool tests. Rather
than one subclass per scenario, tests wire `on_wait_visible` /
`on_click_submit_near` closures (plus a mutable `current_url_value`) to
build a tiny state machine per scenario — keeps the fake generic while
letting each test's scenario stay readable at the call site."""

from typing import Callable

from dealercenter_agent.automation.base import BrowserDriver


class FakeBrowserDriver(BrowserDriver):
    def __init__(self):
        self.current_url_value = "https://app.dealercenter.net/"
        self.filled: dict[str, str] = {}
        self.typed: dict[str, str] = {}
        self.clicks: list[str] = []
        self.submit_clicks: list[str] = []
        self.dismiss_called = False
        self.get_text_value = ""
        self.find_all_text_results: dict[str, list[str]] = {}
        self.find_all_values_results: dict[str, list[str]] = {}
        self.select_calls: list[tuple[str, str]] = []
        self.uploaded_files: list[tuple[str, str]] = []
        self.on_click_submit_near: Callable[[str], None] | None = None
        self.on_wait_visible: Callable[[str, int], bool] | None = None
        self.on_find_all_text: Callable[[str], list[str]] | None = None
        self.on_find_all_values: Callable[[str], list[str]] | None = None
        self.on_click: Callable[[str], None] | None = None
        self.on_wait_hidden: Callable[[str, int], bool] | None = None

    def goto(self, url: str) -> None:
        pass

    def current_url(self) -> str:
        return self.current_url_value

    def fill(self, selector: str, value: str) -> None:
        self.filled[selector] = value

    def type_text(self, selector: str, text: str) -> None:
        self.typed[selector] = text

    def click(self, selector: str) -> None:
        self.clicks.append(selector)
        if self.on_click:
            self.on_click(selector)

    def force_click(self, selector: str) -> None:
        self.clicks.append(selector)
        if self.on_click:
            self.on_click(selector)

    def click_submit_near(self, field_selector: str) -> None:
        self.submit_clicks.append(field_selector)
        if self.on_click_submit_near:
            self.on_click_submit_near(field_selector)

    def wait_visible(self, selector: str, timeout_ms: int = 2000) -> bool:
        if self.on_wait_visible:
            return self.on_wait_visible(selector, timeout_ms)
        return False

    def dismiss_blocking_modals(self, max_rounds: int = 5) -> None:
        self.dismiss_called = True

    def wait_hidden(self, selector: str, timeout_ms: int = 2000) -> bool:
        if self.on_wait_hidden:
            return self.on_wait_hidden(selector, timeout_ms)
        return True

    def get_text(self, selector: str) -> str:
        return self.get_text_value

    def find_all_text(self, selector: str) -> list[str]:
        if self.on_find_all_text:
            return self.on_find_all_text(selector)
        return self.find_all_text_results.get(selector, [])

    def find_all_values(self, selector: str) -> list[str]:
        if self.on_find_all_values:
            return self.on_find_all_values(selector)
        return self.find_all_values_results.get(selector, [])

    def select_option(self, trigger_selector: str, option_text: str) -> None:
        self.select_calls.append((trigger_selector, option_text))

    def upload_via_button(self, trigger_selector: str, file_path: str) -> None:
        self.uploaded_files.append((trigger_selector, file_path))

    def save_session(self) -> dict:
        return {"cookies": [], "origins": []}

    def screenshot(self, path: str) -> None:
        pass
