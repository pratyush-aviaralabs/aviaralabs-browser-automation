import pytest

from dealercenter_agent.tools.base import StepFailure, ToolStatus
from dealercenter_agent.tools.file_upload_tool import (
    CATEGORY_DROPDOWN_SELECTOR,
    FILE_INPUT_SELECTOR,
    FILE_NAME_INPUT_SELECTOR,
    MAX_FILE_SIZE_BYTES,
    TOP_SAVE_BUTTON_SELECTOR,
    FileUploadTool,
)


@pytest.fixture
def small_pdf(tmp_path):
    p = tmp_path / "invoice.pdf"
    p.write_bytes(b"%PDF-1.4 fake pdf content")
    return p


@pytest.fixture
def oversized_pdf(tmp_path):
    p = tmp_path / "huge.pdf"
    p.write_bytes(b"0" * (MAX_FILE_SIZE_BYTES + 1))
    return p


def test_upload_fills_name_category_and_saves(fake_driver_no_matching_files, small_pdf):
    driver = fake_driver_no_matching_files
    result = FileUploadTool().run(
        driver,
        local_pdf_path=str(small_pdf),
        file_name="Jensen 110011350",
        invoice_number="110011350",
    )
    assert result.status == ToolStatus.SUCCESS
    assert (FILE_INPUT_SELECTOR, str(small_pdf)) in driver.uploaded_files
    assert driver.filled[FILE_NAME_INPUT_SELECTOR] == "Jensen 110011350"
    assert (CATEGORY_DROPDOWN_SELECTOR, "Others") in driver.select_calls
    assert TOP_SAVE_BUTTON_SELECTOR in driver.clicks


def test_upload_rejects_file_over_25mb(fake_driver_no_matching_files, oversized_pdf):
    with pytest.raises(StepFailure) as exc:
        FileUploadTool().run(
            fake_driver_no_matching_files,
            local_pdf_path=str(oversized_pdf),
            file_name="X 1",
            invoice_number="1",
        )
    assert "25MB" in exc.value.reason


def test_upload_fails_when_file_missing(fake_driver_no_matching_files, tmp_path):
    missing = tmp_path / "does_not_exist.pdf"
    with pytest.raises(StepFailure) as exc:
        FileUploadTool().run(
            fake_driver_no_matching_files,
            local_pdf_path=str(missing),
            file_name="X 1",
            invoice_number="1",
        )
    assert "does not exist" in exc.value.reason


def test_upload_skips_when_invoice_number_already_in_a_file_name(
    fake_driver_duplicate_file, small_pdf
):
    driver = fake_driver_duplicate_file
    result = FileUploadTool().run(
        driver,
        local_pdf_path=str(small_pdf),
        file_name="Omaha Glass 276289",
        invoice_number="276289",
    )
    assert result.status == ToolStatus.EXISTS
    assert driver.uploaded_files == []
    assert TOP_SAVE_BUTTON_SELECTOR not in driver.clicks


def test_upload_fails_when_files_tab_does_not_load(fake_driver_files_tab_never_loads, small_pdf):
    with pytest.raises(StepFailure) as exc:
        FileUploadTool().run(
            fake_driver_files_tab_never_loads,
            local_pdf_path=str(small_pdf),
            file_name="X 1",
            invoice_number="1",
        )
    assert "Files tab did not load" in exc.value.reason
