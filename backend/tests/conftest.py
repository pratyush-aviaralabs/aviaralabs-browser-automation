from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _block_real_outbound_callbacks(monkeypatch):
    """Nothing in the suite should make a real network call via
    notify_dealercenter_result's best-effort callback POST — no-op it
    here by default. Tests asserting on the payload monkeypatch
    httpx.post themselves, which simply overrides this no-op for that
    test."""
    monkeypatch.setattr("httpx.post", lambda *a, **k: None)


@pytest.fixture(scope="session")
def _browser():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def playwright_page(_browser):
    context = _browser.new_context()
    page = context.new_page()
    yield page
    context.close()


@pytest.fixture
def login_form_fixture_url() -> str:
    return (FIXTURES_DIR / "login_form.html").as_uri()


@pytest.fixture
def modal_chain_fixture_url() -> str:
    return (FIXTURES_DIR / "modal_chain.html").as_uri()


@pytest.fixture
def rows_fixture_url() -> str:
    return (FIXTURES_DIR / "rows.html").as_uri()


@pytest.fixture
def dropdown_fixture_url() -> str:
    return (FIXTURES_DIR / "dropdown.html").as_uri()


@pytest.fixture
def file_input_fixture_url() -> str:
    return (FIXTURES_DIR / "file_input.html").as_uri()


@pytest.fixture
def modal_with_decoy_close_button_fixture_url() -> str:
    return (FIXTURES_DIR / "modal_with_decoy_close_button.html").as_uri()


@pytest.fixture
def modal_covering_trust_browser_button_fixture_url() -> str:
    return (FIXTURES_DIR / "modal_covering_trust_browser_button.html").as_uri()
