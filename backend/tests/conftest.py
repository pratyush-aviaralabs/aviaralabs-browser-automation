from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

FIXTURES_DIR = Path(__file__).parent / "fixtures"


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
