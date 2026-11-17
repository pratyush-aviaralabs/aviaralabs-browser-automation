#!/usr/bin/env python3
"""Manual, human-run test for CostAddTool specifically — all of its
selectors are still unverified guesses, unlike SearchInvoiceTool and
FileUploadTool which have now been corrected through several live
rounds. Not part of the automated test suite — run this yourself, same
reason as every other manual_*.py script here.

Flow: Login -> Search (inventory match only) -> CostAdd. No file upload.

Usage:
  venv/bin/python scripts/manual_cost_add_test.py <vin> <vendor_name> <invoice_number> <cost> <invoice_date:YYYY-MM-DD> <category:Repair|Other> [--skip-duplicate-check] [--manual-save]

--skip-duplicate-check forces the write path even when the memo already
exists in the grid — deliberate, manual-testing-only bypass of
CostAddTool's real duplicate-check (see cost_add_tool.py). Leaves a
real extra row in the live account; delete it via its trash icon
afterward.

--manual-save fills every field (Category, Cost, Vendor, Date, Memo)
but deliberately does NOT click Save — pauses so you can click it
yourself and inspect exactly what does/doesn't persist. Debugging aid
for the intermittent silent-save-failure confirmed live (2026-11-18):
a human clicking Save a moment after filling fields by hand always
persisted correctly, automation's near-instant fill+click intermittently
didn't. Skips verification and the retry loop entirely — it's on you
to check the result.
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
from dealercenter_agent.tools.base import StepFailure
from dealercenter_agent.tools.cost_add_tool import CostAddTool
from dealercenter_agent.tools.login_tool import LoginTool
from dealercenter_agent.tools.search_invoice_tool import SearchInvoiceTool
from dealercenter_agent.tools.verify_cost_add_tool import VerifyCostAddTool

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_FILE = PROJECT_ROOT / ".auth_state.json"
load_dotenv(PROJECT_ROOT / ".env")

# Confirmed live (2026-11-18): a reported SUCCESS save can still
# silently fail to persist server-side — intermittent, cause not fully
# pinned down (force_click's DOM .click() doesn't guarantee the
# underlying save request actually fires every time). Rather than chase
# that further, retry the whole write once more if verification fails.
MAX_SAVE_ATTEMPTS = 2


def terminal_mfa_code_provider() -> str:
    return input("    Enter the 6-digit MFA code here: ").strip()


def main():
    args = sys.argv[1:]
    skip_duplicate_check = "--skip-duplicate-check" in args
    manual_save = "--manual-save" in args
    args = [a for a in args if a not in ("--skip-duplicate-check", "--manual-save")]

    if len(args) != 6:
        print(
            "usage: manual_cost_add_test.py <vin> <vendor_name> <invoice_number> "
            "<cost> <invoice_date:YYYY-MM-DD> <category:Repair|Other> "
            "[--skip-duplicate-check] [--manual-save]"
        )
        return 2
    vin, vendor_name, invoice_number, cost_str, date_str, category = args
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

        # Catches everything, not just StepFailure — a raw Playwright
        # error here must not close the browser before you get a chance
        # to look at whatever page it stopped on, same lesson learned
        # building the web UI's runner.py.
        try:
            search_result = SearchInvoiceTool().run(driver, vin=vin, stock_number=None)
            print(f"[search] {search_result.status} match_type={search_result.data['match_type']}")

            if search_result.data["match_type"] != "inventory":
                print("[!] Deal-path click-through isn't built yet — stopping here.")
            elif manual_save:
                cost_add_result = CostAddTool().run(
                    driver,
                    category=category,
                    cost=cost,
                    invoice_date=invoice_date,
                    vendor_name=vendor_name,
                    memo=invoice_number,
                    skip_duplicate_check=skip_duplicate_check,
                    auto_save=False,
                )
                print(f"[cost_add] {cost_add_result.status} data={cost_add_result.data}")
                print(
                    "[!] Every field is filled; Save was deliberately NOT clicked. "
                    "Click it yourself now, then check which fields actually persisted."
                )
            else:
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

                    # Don't trust CostAddTool's own SUCCESS — confirmed
                    # live (2026-11-18) that a reported SUCCESS save can
                    # still silently fail to persist server-side.
                    verify_result = VerifyCostAddTool().run(
                        driver, memo=invoice_number, expected_cost=cost
                    )
                    print(f"[verify_cost_add] attempt {attempt}: {verify_result.status} "
                          f"{verify_result.data if verify_result.data else verify_result.reason}")

                    if verify_result.status.name == "SUCCESS":
                        break
                    if attempt < MAX_SAVE_ATTEMPTS:
                        print("[!] Verification failed — retrying the write once more.")
        except StepFailure as exc:
            print(f"[FAILED] {exc.tool_name}: {exc.reason}")
        except Exception as exc:
            print(f"[UNEXPECTED ERROR] {exc}")

        input("\nPress Enter to close the browser... ")

    return 0


if __name__ == "__main__":
    sys.exit(main())
