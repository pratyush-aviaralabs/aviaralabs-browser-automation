import pytest

from dealercenter_agent.tools.login_tool import (
    AUTH_HOST,
    BASE_URL,
    MFA_CODE_SELECTOR,
    PASSWORD_SELECTOR,
    SEARCH_BOX_SELECTOR,
    USERNAME_SELECTOR,
)
from dealercenter_agent.tools.search_invoice_tool import (
    DEAL_ROW_SELECTOR,
    INVENTORY_ROW_SELECTOR,
    SEARCH_BOX_SELECTOR as SEARCH_INVOICE_BOX_SELECTOR,
)
from dealercenter_agent.tools.cost_add_tool import EXISTING_MEMO_SELECTOR, VENDOR_RESULT_ROW_SELECTOR
from dealercenter_agent.tools.file_upload_tool import (
    DUPLICATE_FILENAME_ERROR_SELECTOR,
    EXISTING_FILE_NAME_SELECTOR,
    NEXT_PAGE_BUTTON_SELECTOR,
)

from ..fakes import FakeBrowserDriver

LOGIN_URL = f"https://{AUTH_HOST}/u/login"
MFA_URL = f"https://{AUTH_HOST}/u/mfa-email-challenge"
DASHBOARD_URL = f"{BASE_URL}/apps/shell/reports/home"


@pytest.fixture
def fake_driver_already_logged_in() -> FakeBrowserDriver:
    driver = FakeBrowserDriver()
    driver.current_url_value = DASHBOARD_URL
    driver.on_wait_visible = lambda sel, t: "search" in sel.lower() or sel == SEARCH_BOX_SELECTOR
    return driver


def _stage_machine_driver(mfa_accepts: bool) -> FakeBrowserDriver:
    state = {"stage": "login_page"}
    driver = FakeBrowserDriver()

    def current_url() -> str:
        return {"login_page": LOGIN_URL, "mfa_page": MFA_URL, "dashboard": DASHBOARD_URL}[
            state["stage"]
        ]

    def on_submit(_selector: str) -> None:
        if state["stage"] == "login_page":
            state["stage"] = "mfa_page"
        elif state["stage"] == "mfa_page" and mfa_accepts:
            state["stage"] = "dashboard"
        # mfa_accepts=False: stays on mfa_page forever, simulating rejection

    def on_wait_visible(selector: str, _timeout_ms: int) -> bool:
        stage = state["stage"]
        if stage == "dashboard":
            return "search" in selector.lower()
        if stage == "login_page":
            return selector in (USERNAME_SELECTOR, PASSWORD_SELECTOR)
        if stage == "mfa_page":
            return selector == MFA_CODE_SELECTOR
        return False

    driver.current_url = current_url  # type: ignore[method-assign]
    driver.on_click_submit_near = on_submit
    driver.on_wait_visible = on_wait_visible
    return driver


@pytest.fixture
def fake_driver_requires_mfa() -> FakeBrowserDriver:
    return _stage_machine_driver(mfa_accepts=True)


@pytest.fixture
def fake_driver_always_rejects_mfa() -> FakeBrowserDriver:
    return _stage_machine_driver(mfa_accepts=False)


@pytest.fixture
def fake_driver_url_lags_behind_redirect() -> FakeBrowserDriver:
    """Reproduces the exact live bug: immediately after goto(), a client-
    side redirect to the auth host hasn't happened YET, so current_url()
    lies and still reports the pre-redirect app host — UNTIL something has
    actually waited for the page to settle, at which point it tells the
    truth. wait_visible() always reports "not found" (nothing ever
    appears — this driver never actually gets logged in), but the act of
    calling it is what the real wait_for(state="visible") poll spends
    time on live, letting the redirect catch up.

    A caller that reads current_url() before waiting for anything sees
    the stale "not on auth host" read and wrongly reports success; a
    caller that waits first (even for something that never appears) sees
    the real, by-then-accurate auth-host URL."""
    driver = FakeBrowserDriver()
    state = {"waited": False}

    def current_url() -> str:
        return (BASE_URL + "/") if not state["waited"] else LOGIN_URL

    def on_wait_visible(_sel: str, _timeout_ms: int) -> bool:
        state["waited"] = True
        return False  # nothing ever appears

    driver.current_url = current_url  # type: ignore[method-assign]
    driver.on_wait_visible = on_wait_visible
    return driver


@pytest.fixture
def fake_driver_one_inventory_match() -> FakeBrowserDriver:
    driver = FakeBrowserDriver()
    driver.find_all_text_results[INVENTORY_ROW_SELECTOR] = ["2022 FORD F150 ... Last 6 VIN: G16336"]
    driver.on_wait_visible = lambda sel, t: True  # vehicle detail page "loads" after the click
    return driver


@pytest.fixture
def fake_driver_dropdown_renders_late() -> FakeBrowserDriver:
    """Reproduces the exact live bug: find_all_text() is a single-shot
    read, not a poll — calling it immediately after fill(), with no wait
    in between, can read the dropdown before Angular has rendered it.
    Here, find_all_text() returns nothing UNTIL wait_visible() has been
    called at least once (modeling the real render completing during
    that wait) — a caller that reads before waiting sees an empty
    dropdown and wrongly concludes 'not found'."""
    driver = FakeBrowserDriver()
    state = {"waited": False}

    def on_wait_visible(_sel: str, _timeout_ms: int) -> bool:
        state["waited"] = True
        return True

    def on_find_all_text(selector: str) -> list[str]:
        if not state["waited"]:
            return []
        if selector == INVENTORY_ROW_SELECTOR:
            return ["2022 FORD F150 ... Last 6 VIN: G16336"]
        return []

    driver.on_wait_visible = on_wait_visible
    driver.on_find_all_text = on_find_all_text
    return driver


@pytest.fixture
def fake_driver_falls_back_to_stock_number() -> FakeBrowserDriver:
    """VIN tier finds nothing in either block; stock-number tier finds a
    single Inventory match. Proves the two-tier fallback actually tries
    tier 2 instead of stopping at tier 1's clean zero-match."""
    driver = FakeBrowserDriver()

    def on_find_all_text(selector: str) -> list[str]:
        last_query = driver.filled.get(SEARCH_INVOICE_BOX_SELECTOR)
        if selector == INVENTORY_ROW_SELECTOR and last_query == "22421":
            return ["2022 FORD F150 ... Stock #: 22421"]
        return []

    driver.on_find_all_text = on_find_all_text
    driver.on_wait_visible = lambda sel, t: True
    return driver


@pytest.fixture
def fake_driver_only_deal_match() -> FakeBrowserDriver:
    driver = FakeBrowserDriver()
    driver.find_all_text_results[INVENTORY_ROW_SELECTOR] = []
    driver.find_all_text_results[DEAL_ROW_SELECTOR] = ["Deal #: 16228 ... Last 6 VIN: G16336"]
    return driver


@pytest.fixture
def fake_driver_no_matches() -> FakeBrowserDriver:
    driver = FakeBrowserDriver()
    driver.find_all_text_results[INVENTORY_ROW_SELECTOR] = []
    driver.find_all_text_results[DEAL_ROW_SELECTOR] = []
    return driver


@pytest.fixture
def fake_driver_two_inventory_matches() -> FakeBrowserDriver:
    driver = FakeBrowserDriver()
    driver.find_all_text_results[INVENTORY_ROW_SELECTOR] = ["row one", "row two"]
    return driver


def _vendor_dialog_driver(filtered_results: list[str]) -> FakeBrowserDriver:
    """Models the real dialog: the FIRST read (before typing a query)
    sees the unfiltered ~98-vendor baseline; every read after that sees
    the already-filtered result — same shape as the real async-filter
    race _read_vendor_results_with_retry exists to survive, without
    wasting real retry/sleep time in the test."""
    driver = FakeBrowserDriver()
    driver.on_wait_visible = lambda sel, t: True
    state = {"calls": 0}

    def on_find_all_text(selector: str) -> list[str]:
        if selector != VENDOR_RESULT_ROW_SELECTOR:
            return []
        state["calls"] += 1
        return [f"UNFILTERED VENDOR {i}" for i in range(98)] if state["calls"] == 1 else filtered_results

    driver.on_find_all_text = on_find_all_text
    return driver


@pytest.fixture
def fake_driver_cost_add_vendor_found() -> FakeBrowserDriver:
    driver = _vendor_dialog_driver(["JENSEN TIRE & AUTO"])
    driver.find_all_values_results[EXISTING_MEMO_SELECTOR] = ["00294", "", ""]  # no duplicate
    return driver


@pytest.fixture
def fake_driver_cost_add_duplicate_memo() -> FakeBrowserDriver:
    driver = FakeBrowserDriver()
    driver.on_wait_visible = lambda sel, t: True
    # Space in the existing memo on purpose — the check must ignore spaces.
    driver.find_all_values_results[EXISTING_MEMO_SELECTOR] = ["276 289", ""]
    return driver


@pytest.fixture
def fake_driver_cost_add_no_vendor_match() -> FakeBrowserDriver:
    return _vendor_dialog_driver([])


@pytest.fixture
def fake_driver_cost_add_ambiguous_vendor() -> FakeBrowserDriver:
    return _vendor_dialog_driver(["Jensen Tire", "Jensen Auto Body"])


@pytest.fixture
def fake_driver_cost_add_modal_never_opens() -> FakeBrowserDriver:
    driver = FakeBrowserDriver()
    driver.on_wait_visible = lambda sel, t: False
    return driver


@pytest.fixture
def fake_driver_no_matching_files() -> FakeBrowserDriver:
    driver = FakeBrowserDriver()
    # Everything "appears" except the duplicate-filename error dialog —
    # a bare lambda-True-for-everything would wrongly trigger that
    # branch too and turn every "normal success" test into EXISTS.
    driver.on_wait_visible = lambda sel, t: sel != DUPLICATE_FILENAME_ERROR_SELECTOR
    # The new upload "appears" only after upload_via_button has actually
    # been called — models the real grid, where the row doesn't exist
    # until the upload happens, so wait_for_file_across_pages' first
    # poll (not a 20s real timeout) is what finds it in the success tests.
    def on_find_all_text(selector: str) -> list[str]:
        if selector != EXISTING_FILE_NAME_SELECTOR:
            return []
        if driver.uploaded_files:
            return ["Monson 00294", "invoice"]
        return ["Monson 00294"]

    driver.on_find_all_text = on_find_all_text
    return driver


@pytest.fixture
def fake_driver_duplicate_file() -> FakeBrowserDriver:
    driver = FakeBrowserDriver()
    driver.on_wait_visible = lambda sel, t: sel != DUPLICATE_FILENAME_ERROR_SELECTOR
    driver.find_all_text_results[EXISTING_FILE_NAME_SELECTOR] = ["Omaha Glass 276289"]
    return driver


@pytest.fixture
def fake_driver_duplicate_filename_rejected_by_server() -> FakeBrowserDriver:
    """No invoice-number match in the grid beforehand, but DealerCenter's
    own server rejects the upload itself as a literal-filename
    collision — a different duplicate signal than our invoice-number
    check, confirmed live (2026-11-17). This is what previously produced
    a false SUCCESS: a broad text= check matched the filename quoted
    inside this very error dialog."""
    driver = FakeBrowserDriver()
    driver.on_wait_visible = lambda sel, t: sel in (
        EXISTING_FILE_NAME_SELECTOR,
        DUPLICATE_FILENAME_ERROR_SELECTOR,
    )
    driver.find_all_text_results[EXISTING_FILE_NAME_SELECTOR] = ["Monson 00294"]
    return driver


@pytest.fixture
def fake_driver_files_tab_never_loads() -> FakeBrowserDriver:
    driver = FakeBrowserDriver()
    driver.on_wait_visible = lambda sel, t: False
    return driver


@pytest.fixture
def fake_driver_duplicate_on_page_two() -> FakeBrowserDriver:
    """Reproduces exactly the live bug: page 1 has no match at all;
    the duplicate-named file only exists on page 2. A single-page read
    would wrongly report 'not a duplicate' and re-upload — this is what
    actually happened 3 times before pagination-walking was added."""
    driver = FakeBrowserDriver()
    pages = [["Monson 00294", "O'Reilly 103355"], ["Jensen 110011300", "O'Reilly 104253"]]
    state = {"page": 0}

    def on_find_all_text(selector: str) -> list[str]:
        if selector == EXISTING_FILE_NAME_SELECTOR:
            return pages[state["page"]]
        return []

    def on_click(selector: str) -> None:
        if selector == NEXT_PAGE_BUTTON_SELECTOR and state["page"] < len(pages) - 1:
            state["page"] += 1

    def on_wait_visible(selector: str, _timeout_ms: int) -> bool:
        if selector == EXISTING_FILE_NAME_SELECTOR:
            return True
        if selector == NEXT_PAGE_BUTTON_SELECTOR:
            return state["page"] < len(pages) - 1  # no next-page control on the last page
        return False

    driver.on_find_all_text = on_find_all_text
    driver.on_click = on_click
    driver.on_wait_visible = on_wait_visible
    return driver


@pytest.fixture
def fake_driver_upload_never_appears(monkeypatch) -> FakeBrowserDriver:
    """Files tab loads fine (so the duplicate check can run), but the
    post-upload wait for the new filename never finds it — models a
    silent upload failure (wrong trigger element, network hiccup, etc.).
    Shrinks the real poll timeout so this expected-failure path doesn't
    burn 20 real seconds in the test suite."""
    import dealercenter_agent.tools.file_upload_tool as file_upload_tool_module

    monkeypatch.setattr(file_upload_tool_module, "UPLOAD_CONFIRM_TIMEOUT_MS", 50)
    monkeypatch.setattr(file_upload_tool_module, "UPLOAD_CONFIRM_POLL_MS", 10)

    driver = FakeBrowserDriver()

    def on_wait_visible(selector: str, _timeout_ms: int) -> bool:
        return selector == EXISTING_FILE_NAME_SELECTOR

    driver.on_wait_visible = on_wait_visible
    driver.find_all_text_results[EXISTING_FILE_NAME_SELECTOR] = ["Monson 00294"]
    return driver
