#!/usr/bin/env python3
"""Manual, human-run end-to-end pipeline check against the REAL
DealerCenter site. Not part of the automated test suite — run this
yourself, same reason as manual_search_test.py (I can't execute real
login against a live third-party account).

Scope matches the current web pipeline exactly: Login -> Search ->
FileUpload -> Verify. ClassifyCategoryTool/CostAddTool are deliberately
left out here — they're still fully unverified against the live DOM,
and this script's job right now is testing the upload flow specifically.

Usage:
  venv/bin/python scripts/manual_pipeline_test.py <vin> <vendor_name> <invoice_number> <pdf_path>
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from dealercenter_agent.automation.playwright_driver import create_playwright_driver
from dealercenter_agent.tools.base import StepFailure, ToolStatus
from dealercenter_agent.tools.file_upload_tool import FileUploadTool
from dealercenter_agent.tools.login_tool import LoginTool
from dealercenter_agent.tools.search_invoice_tool import SearchInvoiceTool
from dealercenter_agent.tools.verify_file_upload_tool import VerifyFileUploadTool

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_FILE = PROJECT_ROOT / ".auth_state.json"
load_dotenv(PROJECT_ROOT / ".env")


def terminal_mfa_code_provider() -> str:
    return input("    Enter the 6-digit MFA code here: ").strip()


def main():
    if len(sys.argv) != 5:
        print("usage: manual_pipeline_test.py <vin> <vendor_name> <invoice_number> <pdf_path>")
        return 2
    vin, vendor_name, invoice_number, pdf_path = sys.argv[1:5]

    saved_state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else None

    with create_playwright_driver(headless=False, storage_state=saved_state) as driver:
        login_result = LoginTool().run(
            driver,
            username=os.environ["DEALER_CENTER_USERNAME"],
            password=os.environ["DEALER_CENTER_PASSWORD"],
            mfa_code_provider=terminal_mfa_code_provider,
        )
        print(f"[login] {login_result.status} reused_session={login_result.data.get('reused_session')}")
        STATE_FILE.write_text(json.dumps(driver.save_session()))

        try:
            search_result = SearchInvoiceTool().run(driver, vin=vin, stock_number=None)
        except StepFailure as exc:
            print(f"[search] FAILED: {exc.reason}")
            input("\nPress Enter to close the browser... ")
            return 1
        print(f"[search] {search_result.status} match_type={search_result.data['match_type']}")
        print(f"[search] row_text={search_result.data['row_text']!r}")

        if search_result.data["match_type"] != "inventory":
            print("[!] Deal-path click-through isn't built yet — stopping here.")
            input("\nPress Enter to close the browser... ")
            return 1

        try:
            upload_result = FileUploadTool().run(
                driver, local_pdf_path=pdf_path, invoice_number=invoice_number
            )
            print(f"[upload] {upload_result.status} data={upload_result.data}")
            if upload_result.status == ToolStatus.SUCCESS:
                verify_result = VerifyFileUploadTool().run(
                    driver, file_name=upload_result.data["uploaded_as"]
                )
                print(f"[verify] {verify_result.status} reason={verify_result.reason}")
        except StepFailure as exc:
            print(f"[upload] FAILED: {exc.reason}")
        except Exception as exc:
            print(f"[upload] UNEXPECTED ERROR: {exc}")

        input("\nPress Enter to close the browser... ")

    return 0


if __name__ == "__main__":
    sys.exit(main())
