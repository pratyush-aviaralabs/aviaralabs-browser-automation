from dealercenter_agent.automation.playwright_driver import PlaywrightDriver


def test_click_submit_near_skips_aria_hidden_button(playwright_page, login_form_fixture_url):
    driver = PlaywrightDriver(playwright_page)
    driver.goto(login_form_fixture_url)
    driver.fill("#username", "x")
    driver.fill("#password", "y")
    driver.click_submit_near("#password")
    assert playwright_page.evaluate("window.__submitted_button_aria_hidden__") == "false"


def test_wait_visible_polls_for_async_modal(playwright_page, modal_chain_fixture_url):
    driver = PlaywrightDriver(playwright_page)
    driver.goto(modal_chain_fixture_url)
    # Modal is injected ~1.5s after load; a non-polling check would miss it.
    assert driver.wait_visible("button:has-text('trust this browser')", timeout_ms=3000) is True


def test_wait_visible_returns_false_when_nothing_appears(playwright_page, modal_chain_fixture_url):
    driver = PlaywrightDriver(playwright_page)
    driver.goto(modal_chain_fixture_url)
    assert driver.wait_visible("button:has-text('this never appears')", timeout_ms=500) is False


def test_dismiss_blocking_modals_accepts_trust_browser_and_clears_overlay(
    playwright_page, modal_chain_fixture_url
):
    driver = PlaywrightDriver(playwright_page)
    driver.goto(modal_chain_fixture_url)
    driver.dismiss_blocking_modals()
    assert playwright_page.evaluate("window.__trusted__") is True
    assert driver.wait_visible(".k-overlay", timeout_ms=500) is False


def test_save_session_returns_storage_state_dict(playwright_page, login_form_fixture_url):
    driver = PlaywrightDriver(playwright_page)
    driver.goto(login_form_fixture_url)
    state = driver.save_session()
    assert "cookies" in state
    assert "origins" in state


def test_find_all_text_returns_one_entry_per_match(playwright_page, rows_fixture_url):
    driver = PlaywrightDriver(playwright_page)
    driver.goto(rows_fixture_url)
    assert driver.find_all_text(".row") == ["Row A", "Row B", "Row C"]


def test_select_option_opens_dropdown_and_clicks_matching_text(
    playwright_page, dropdown_fixture_url
):
    driver = PlaywrightDriver(playwright_page)
    driver.goto(dropdown_fixture_url)
    driver.select_option("#trigger", "Repair")
    assert playwright_page.evaluate("window.__selected__") == "Repair"


def test_upload_via_button_triggers_file_chooser(playwright_page, file_input_fixture_url, tmp_path):
    pdf = tmp_path / "invoice.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    driver = PlaywrightDriver(playwright_page)
    driver.goto(file_input_fixture_url)
    driver.upload_via_button("#browse", str(pdf))
    assert playwright_page.evaluate("window.__uploaded_name__") == "invoice.pdf"
