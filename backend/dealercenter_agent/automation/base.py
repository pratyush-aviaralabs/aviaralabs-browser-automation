from abc import ABC, abstractmethod


class BrowserDriver(ABC):
    """Everything a Tool needs from a browser, with zero Playwright-specific
    types leaking through — swapping the engine later means writing one new
    subclass, no changes to any Tool."""

    @abstractmethod
    def goto(self, url: str) -> None: ...

    @abstractmethod
    def current_url(self) -> str: ...

    @abstractmethod
    def fill(self, selector: str, value: str) -> None: ...

    @abstractmethod
    def click(self, selector: str) -> None: ...

    @abstractmethod
    def click_submit_near(self, field_selector: str) -> None:
        """Click the real submit button in the same <form> as the given
        field — not just any button matching a text/type selector. See
        PlaywrightDriver for why this distinction matters."""
        ...

    @abstractmethod
    def wait_visible(self, selector: str, timeout_ms: int = 2000) -> bool:
        """Poll for the element to become visible; return False on timeout
        rather than raising. Must actually wait — a single-shot check here
        is the bug class this method exists to prevent."""
        ...

    @abstractmethod
    def dismiss_blocking_modals(self, max_rounds: int = 5) -> None: ...

    @abstractmethod
    def get_text(self, selector: str) -> str: ...

    @abstractmethod
    def save_session(self) -> dict:
        """Capture the current authenticated state for persistence.
        There is deliberately no matching load_session(state) here —
        Playwright only accepts storage_state at context-creation time,
        not onto an already-open page, so loading a saved session is a
        driver-construction concern (see create_playwright_driver), not a
        per-instance method every BrowserDriver would have to fake."""
        ...

    @abstractmethod
    def screenshot(self, path: str) -> None: ...
