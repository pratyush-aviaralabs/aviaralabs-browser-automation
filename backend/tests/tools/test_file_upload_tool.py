import pytest

from dealercenter_agent.tools.base import StepFailure, ToolStatus
from dealercenter_agent.tools.file_upload_tool import (
    BROWSE_BUTTON_SELECTOR,
    MAX_FILE_SIZE_BYTES,
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


def test_upload_clicks_browse_with_the_real_file(fake_driver_no_matching_files, small_pdf):
    driver = fake_driver_no_matching_files
    result = FileUploadTool().run(
        driver,
        local_pdf_path=str(small_pdf),
        invoice_number="110011350",
    )
    assert result.status == ToolStatus.SUCCESS
    assert (BROWSE_BUTTON_SELECTOR, str(small_pdf)) in driver.uploaded_files
    assert result.data["uploaded_as"] == "invoice"
    assert result.data["category"] == "Others"


def test_upload_rejects_file_over_25mb(fake_driver_no_matching_files, oversized_pdf):
    with pytest.raises(StepFailure) as exc:
        FileUploadTool().run(
            fake_driver_no_matching_files,
            local_pdf_path=str(oversized_pdf),
            invoice_number="1",
        )
    assert "25MB" in exc.value.reason


def test_upload_fails_when_file_missing(fake_driver_no_matching_files, tmp_path):
    missing = tmp_path / "does_not_exist.pdf"
    with pytest.raises(StepFailure) as exc:
        FileUploadTool().run(
            fake_driver_no_matching_files,
            local_pdf_path=str(missing),
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
        invoice_number="276289",
    )
    assert result.status == ToolStatus.EXISTS
    assert driver.uploaded_files == []


def test_upload_finds_duplicate_sitting_only_on_page_two(fake_driver_duplicate_on_page_two, small_pdf):
    # Reproduces the actual live bug: page 1 alone has no match; the
    # duplicate only exists on page 2. Must walk pages, not just read page 1.
    driver = fake_driver_duplicate_on_page_two
    result = FileUploadTool().run(
        driver,
        local_pdf_path=str(small_pdf),
        invoice_number="110011300",
    )
    assert result.status == ToolStatus.EXISTS
    assert driver.uploaded_files == []


def test_upload_fails_when_files_tab_does_not_load(fake_driver_files_tab_never_loads, small_pdf):
    with pytest.raises(StepFailure) as exc:
        FileUploadTool().run(
            fake_driver_files_tab_never_loads,
            local_pdf_path=str(small_pdf),
            invoice_number="1",
        )
    assert "Files tab did not load" in exc.value.reason


def test_upload_fails_when_file_never_appears_in_grid(fake_driver_upload_never_appears, small_pdf):
    with pytest.raises(StepFailure) as exc:
        FileUploadTool().run(
            fake_driver_upload_never_appears,
            local_pdf_path=str(small_pdf),
            invoice_number="1",
        )
    assert "never appeared in the Files grid" in exc.value.reason
