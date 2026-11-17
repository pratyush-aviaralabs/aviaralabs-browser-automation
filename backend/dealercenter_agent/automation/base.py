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
    def select_option(self, trigger_selector: str, option_text: str) -> None:
        """Open a custom (e.g. Kendo) dropdown at trigger_selector and
        click the option matching option_text. One gesture, not
        click()+click(), because the options list is typically rendered
        into a popup that isn't nested under the trigger in the DOM —
        composing it from two generic clicks would make every Tool that
        uses a dropdown re-derive that same engine-specific detail."""
        ...

    @abstractmethod
    def upload_via_button(self, trigger_selector: str, file_path: str) -> None:
        """Click a button that opens the browser's native file-chooser
        dialog and supply file_path to it. Confirmed live (2026-11-17)
        that DealerCenter's upload dropzone has no plain
        <input type="file"> sitting in the DOM at rest — clicking its
        Browse button is what the real flow actually requires."""
        ...

    @abstractmethod
    def get_text(self, selector: str) -> str: ...

    @abstractmethod
    def find_all_text(self, selector: str) -> list[str]:
        """Text of every element matching selector, one entry per match —
        how a Tool counts rows (one match vs. ambiguous vs. none) instead
        of parsing a single blob of concatenated text."""
        ...

    @abstractmethod
    def find_all_values(self, selector: str) -> list[str]:
        """Like find_all_text, but reads each matched element's `value`
        property instead of its text content. Confirmed live (2026-11-17)
        that DealerCenter's Cost Adds grid renders the Memo (and every
        other field) as an Angular-bound <input formcontrolname="...">
        with no visible text node — find_all_text's inner-text read
        returns "" for these regardless of selector correctness."""
        ...

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
