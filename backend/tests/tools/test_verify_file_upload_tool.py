from dealercenter_agent.tools.base import ToolStatus
from dealercenter_agent.tools.file_upload_tool import EXISTING_FILE_NAME_SELECTOR
from dealercenter_agent.tools.verify_file_upload_tool import VerifyFileUploadTool

from ..fakes import FakeBrowserDriver


def test_verify_succeeds_when_file_name_listed():
    driver = FakeBrowserDriver()
    driver.find_all_text_results[EXISTING_FILE_NAME_SELECTOR] = [
        "Monson 00294",
        "Jensen 110011350",
    ]
    result = VerifyFileUploadTool().run(driver, file_name="Jensen 110011350")
    assert result.status == ToolStatus.SUCCESS


def test_verify_fails_when_file_name_missing_from_grid():
    driver = FakeBrowserDriver()
    driver.find_all_text_results[EXISTING_FILE_NAME_SELECTOR] = ["Monson 00294"]
    result = VerifyFileUploadTool().run(driver, file_name="Jensen 110011350")
    assert result.status == ToolStatus.FAILED
    assert result.reason == "PDF not listed on Files"


def test_verify_waits_for_the_row_before_reading_the_grid():
    """Reproduces the exact live bug: find_all_text() is a single-shot
    read. The row isn't in find_all_text_results until wait_visible()
    has actually been called — a caller that reads before waiting sees
    an empty/stale grid and wrongly reports FAILED."""
    driver = FakeBrowserDriver()
    state = {"waited": False}

    def on_wait_visible(_sel: str, _timeout_ms: int) -> bool:
        state["waited"] = True
        return True

    def on_find_all_text(selector: str) -> list[str]:
        if selector == EXISTING_FILE_NAME_SELECTOR and state["waited"]:
            return ["Jensen 110011350"]
        return []

    driver.on_wait_visible = on_wait_visible
    driver.on_find_all_text = on_find_all_text

    result = VerifyFileUploadTool().run(driver, file_name="Jensen 110011350")
    assert result.status == ToolStatus.SUCCESS
