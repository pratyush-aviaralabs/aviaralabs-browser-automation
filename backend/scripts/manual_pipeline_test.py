#!/usr/bin/env python3
"""Manual, human-run end-to-end pipeline check against the REAL
DealerCenter site, using one real invoice's extracted data. Not part of
the automated test suite — run this yourself, same reason as
manual_search_test.py (I can't execute real login against a live
third-party account).

Usage:
  venv/bin/python scripts/manual_pipeline_test.py [path/to/invoice.pdf]

If no PDF path is given, FileUploadTool/VerifyFileUploadTool are skipped
and everything up through CostAddTool still runs.
"""
import json
import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from dealercenter_agent.automation.playwright_driver import create_playwright_driver
from dealercenter_agent.tools.base import StepFailure, ToolStatus
from dealercenter_agent.tools.classify_category_tool import ClassifyCategoryTool
from dealercenter_agent.tools.cost_add_tool import CostAddTool
from dealercenter_agent.tools.file_upload_tool import FileUploadTool
from dealercenter_agent.tools.login_tool import LoginTool
from dealercenter_agent.tools.search_invoice_tool import SearchInvoiceTool
from dealercenter_agent.tools.verify_file_upload_tool import VerifyFileUploadTool

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_FILE = PROJECT_ROOT / ".auth_state.json"
load_dotenv(PROJECT_ROOT / ".env")

# This one invoice's extracted fields, from the Slack thread.
INVOICE = {
    "full_vin": "1FTCR15X1LPA67507",
    "invoice_number": "110011300",
    "invoice_date": date(2026, 10, 22),
    "vendor_name": "Jensen Tire & Auto",
    "grand_total": Decimal("119.12"),  # NOT the line-item sum (120.30) — see skill rule
    "line_items": [
        "Semi-Synthetic Oil Change",
        "Oil Filter",
        "5W30 Semi Synthetic Engine Oil",
        "Labor",
        "Service Package Savings",
        "Wiper Blades",
        "Front Wiper Blades",
        "Air Filter",
        "Air Filter, 6126/NAP1",
    ],
}


def terminal_mfa_code_provider() -> str:
    return input("    Enter the 6-digit MFA code here: ").strip()


def main():
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else None
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
            search_result = SearchInvoiceTool().run(
                driver, vin=INVOICE["full_vin"], stock_number=None
            )
        except StepFailure as exc:
            print(f"[search] FAILED: {exc.reason}")
            print("No PO-number fallback tier exists yet — this is as far as this run goes.")
            input("\nPress Enter to close the browser... ")
            return 1
        print(f"[search] {search_result.status} match_type={search_result.data['match_type']}")
        print(f"[search] row_text={search_result.data['row_text']!r}")

        if search_result.data["match_type"] != "inventory":
            print("[!] Deal-path click-through isn't built yet — stopping here.")
            input("\nPress Enter to close the browser... ")
            return 1

        classify_result = ClassifyCategoryTool().run(driver, line_items=INVOICE["line_items"])
        category = classify_result.data["category"]
        print(f"[classify] category={category}")

        try:
            cost_add_result = CostAddTool().run(
                driver,
                category=category,
                cost=INVOICE["grand_total"],
                invoice_date=INVOICE["invoice_date"],
                vendor_name=INVOICE["vendor_name"],
                memo=INVOICE["invoice_number"],
            )
        except StepFailure as exc:
            print(f"[cost_add] FAILED: {exc.reason}")
            input("\nPress Enter to close the browser... ")
            return 1
        print(f"[cost_add] {cost_add_result.status} data={cost_add_result.data}")

        if cost_add_result.status == ToolStatus.EXISTS:
            print("[cost_add] Already exists — matches the dedup rule, nothing more to do.")
        elif pdf_path:
            file_name = f"{INVOICE['vendor_name'].split()[0]} {INVOICE['invoice_number']}"
            try:
                upload_result = FileUploadTool().run(
                    driver,
                    local_pdf_path=pdf_path,
                    file_name=file_name,
                    invoice_number=INVOICE["invoice_number"],
                )
                print(f"[upload] {upload_result.status} data={upload_result.data}")
                if upload_result.status == ToolStatus.SUCCESS:
                    verify_result = VerifyFileUploadTool().run(driver, file_name=file_name)
                    print(f"[verify] {verify_result.status} reason={verify_result.reason}")
            except StepFailure as exc:
                print(f"[upload] FAILED: {exc.reason}")
        else:
            print("[upload] Skipped — no PDF path given on the command line.")

        input("\nPress Enter to close the browser... ")

    return 0


if __name__ == "__main__":
    sys.exit(main())
