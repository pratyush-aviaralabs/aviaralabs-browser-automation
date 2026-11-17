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
