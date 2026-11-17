from abc import ABC, abstractmethod


class BrowserDriver(ABC):
    """Everything a Tool needs from a browser, with zero Playwright-specific
    types leaking through — swapping the engine later means writing one new
    subclass, no changes to any Tool."""

    @abstractmethod
    def goto(self, url: str) -> None: ...

    @abstractmethod
    def enter_iframe(self, selector: str) -> None:
        """From here on, every selector-based call (click, fill,
        find_all_text, etc.) resolves inside the <iframe> matching
        selector instead of the top-level page — until exit_iframe().
        Confirmed live (2026-11-18): a Deal match's entire "Edit
        Vehicle" surface (Cost Adds, vendor search, Save Confirmation —
        everything CostAddTool/FileUploadTool touch) renders inside
        DealerCenter's own <iframe id="dc-iframe-dialog">, while the
        Inventory path's equivalent page never does. This lets
        SearchInvoiceTool switch context once after landing on a Deal's
        Edit Vehicle modal, so every downstream Tool keeps working
        completely unchanged — none of their selectors need to know
        whether they're inside an iframe or not."""
        ...

    @abstractmethod
    def exit_iframe(self) -> None:
        """Return to resolving selectors against the top-level page."""
        ...

    @abstractmethod
    def current_url(self) -> str: ...

    @abstractmethod
    def fill(self, selector: str, value: str) -> None: ...

    @abstractmethod
    def type_text(self, selector: str, text: str) -> None:
        """Click the element, select all existing content, and type text
        as real keystrokes — unlike fill(), which sets the DOM value
        property directly. Required for masked inputs (e.g. Kendo date
        pickers): confirmed live (2026-11-18) that fill() on one
        produced corrupted text ("11/18/0006" instead of "11/18/2026"),
        since direct value assignment bypasses the input's own mask
        logic that keystroke-by-keystroke typing goes through."""
        ...

    @abstractmethod
    def click(self, selector: str) -> None: ...

    @abstractmethod
    def force_click(self, selector: str) -> None:
        """A real, trusted click (same mechanism as an actual human
        click) that skips Playwright's own pre-click "is anything
        blocking this" wait — only for a target already confirmed
        visible/enabled/stable, where the caller has already dealt with
        (or deliberately chosen to skip past) a known transient blocker.
        Confirmed live (2026-11-18): calling the DOM element's own
        .click() method instead (bypassing coordinates entirely) fires
        an UNTRUSTED event (isTrusted: false) — no automated run using
        that ever showed the app's own save-confirmation toast, while a
        genuine human click always did and always persisted. Use this,
        not a DOM-method click, and have the caller wait out any real
        blocking overlay first rather than relying on this to paper
        over one."""
        ...

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
    def wait_hidden(self, selector: str, timeout_ms: int = 2000) -> bool:
        """Poll for the element to become hidden or detached from the
        DOM; return False on timeout rather than raising. Needed because
        closing a Kendo dialog doesn't mean its backdrop overlay has
        finished its closing animation yet — confirmed live (2026-11-18)
        that clicking Save immediately after a dialog visibly closed
        still got blocked for 30s by that dialog's own leftover
        k-overlay, still mid-"appear" transition."""
        ...

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
