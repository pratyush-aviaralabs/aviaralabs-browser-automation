#!/usr/bin/env python3
"""Manual, human-run end-to-end pipeline check against the REAL
DealerCenter site. Not part of the automated test suite — run this
yourself, same reason as every other manual_*.py script here.

Flow: Login -> Search -> CostAdd -> FileUpload -> Verify. Both
Inventory and Deal matches click through to CostAddTool/FileUploadTool
(the "Edit Vehicle" modal a Deal match lands on is functionally the
same vehicle-detail surface, confirmed live 2026-11-18).

Usage:
  venv/bin/python scripts/manual_pipeline_test.py <vin> <vendor_name> <invoice_number> <cost> <invoice_date:YYYY-MM-DD> <category:Repair|Other> <pdf_path> [--skip-duplicate-check]

--skip-duplicate-check forces CostAddTool's write path even when the
memo already exists in the Cost Adds grid — deliberate,
manual-testing-only bypass (see cost_add_tool.py). Leaves a real extra
row in the live account; delete it via its trash icon afterward.
"""
import json
import os
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from dealercenter_agent.automation.playwright_driver import create_playwright_driver
from dealercenter_agent.tools.base import StepFailure, ToolStatus
from dealercenter_agent.tools.cost_add_tool import CostAddTool
from dealercenter_agent.tools.file_upload_tool import FileUploadTool
from dealercenter_agent.tools.login_tool import LoginTool
from dealercenter_agent.tools.search_invoice_tool import SearchInvoiceTool
from dealercenter_agent.tools.verify_cost_add_tool import VerifyCostAddTool
from dealercenter_agent.tools.verify_file_upload_tool import VerifyFileUploadTool

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_FILE = PROJECT_ROOT / ".auth_state.json"
load_dotenv(PROJECT_ROOT / ".env")

# Confirmed live (2026-11-18): a reported SUCCESS save can still
# silently fail to persist server-side — intermittent, cause not fully
# pinned down. Retry the whole CostAddTool write once more if
# verification fails, same as manual_cost_add_test.py.
MAX_SAVE_ATTEMPTS = 2


def terminal_mfa_code_provider() -> str:
    return input("    Enter the 6-digit MFA code here: ").strip()


def main():
    args = sys.argv[1:]
    skip_duplicate_check = "--skip-duplicate-check" in args
    args = [a for a in args if a != "--skip-duplicate-check"]

    if len(args) != 7:
        print(
            "usage: manual_pipeline_test.py <vin> <vendor_name> <invoice_number> "
            "<cost> <invoice_date:YYYY-MM-DD> <category:Repair|Other> <pdf_path> "
            "[--skip-duplicate-check]"
        )
        return 2
    vin, vendor_name, invoice_number, cost_str, date_str, category, pdf_path = args
    cost = Decimal(cost_str)
    invoice_date = datetime.strptime(date_str, "%Y-%m-%d").date()

    if skip_duplicate_check:
        print(
            "[!] --skip-duplicate-check is on: this WILL add a real duplicate "
            "entry to the live account if the invoice already exists there."
        )

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

        # Deal matches now click through to the same "Edit Vehicle"
        # modal CostAddTool/FileUploadTool already work against
        # (confirmed live 2026-11-18) — both match types proceed the
        # same way from here.

        # Catches everything, not just StepFailure — a raw Playwright
        # error here must not close the browser before you get a chance
        # to look at whatever page it stopped on.
        try:
            for attempt in range(1, MAX_SAVE_ATTEMPTS + 1):
                cost_add_result = CostAddTool().run(
                    driver,
                    category=category,
                    cost=cost,
                    invoice_date=invoice_date,
                    vendor_name=vendor_name,
                    memo=invoice_number,
                    skip_duplicate_check=skip_duplicate_check,
                )
                print(f"[cost_add] attempt {attempt}: {cost_add_result.status} "
                      f"data={cost_add_result.data}")

                if cost_add_result.status.name != "SUCCESS":
                    break  # EXISTS or anything else — nothing new was saved, don't retry

                verify_result = VerifyCostAddTool().run(
                    driver, memo=invoice_number, expected_cost=cost
                )
                print(f"[verify_cost_add] attempt {attempt}: {verify_result.status} "
                      f"{verify_result.data if verify_result.data else verify_result.reason}")

                if verify_result.status.name == "SUCCESS":
                    break
                if attempt < MAX_SAVE_ATTEMPTS:
                    print("[!] Cost Add verification failed — retrying the write once more.")

            upload_result = FileUploadTool().run(
                driver, local_pdf_path=pdf_path, invoice_number=invoice_number
            )
            print(f"[upload] {upload_result.status} data={upload_result.data}")
            if upload_result.status == ToolStatus.SUCCESS:
                verify_upload_result = VerifyFileUploadTool().run(
                    driver, file_name=upload_result.data["uploaded_as"]
                )
                print(f"[verify_upload] {verify_upload_result.status} reason={verify_upload_result.reason}")
        except StepFailure as exc:
            print(f"[FAILED] {exc.tool_name}: {exc.reason}")
        except Exception as exc:
            print(f"[UNEXPECTED ERROR] {exc}")

        input("\nPress Enter to close the browser... ")

    return 0


if __name__ == "__main__":
    sys.exit(main())
