#!/usr/bin/env python3
"""Manual, human-run check against the REAL DealerCenter site. Not part of
the automated test suite (nothing here is a fake/fixture) — run this
yourself when you want to confirm SearchInvoiceTool's selectors against
the live DOM, since that's the one thing the fake-driver tests can't prove.

Usage:
  venv/bin/python scripts/manual_search_test.py <vin_or_stock_number>

Reuses the same .auth_state.json the POC script already proved works, so
if that session is still valid this should skip straight past login/MFA.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from dealercenter_agent.automation.playwright_driver import create_playwright_driver
from dealercenter_agent.automation.session_state import load_saved_state, save_state
from dealercenter_agent.tools.login_tool import LoginTool
from dealercenter_agent.tools.search_invoice_tool import (
    SEARCH_BOX_SELECTOR,
    SEARCH_RESULT_ROW_SELECTOR,
    SearchInvoiceTool,
)
from dealercenter_agent.tools.base import StepFailure

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_FILE = PROJECT_ROOT / ".auth_state.json"

load_dotenv(PROJECT_ROOT / ".env")


def terminal_mfa_code_provider() -> str:
    return input("    Enter the 6-digit MFA code here: ").strip()


def main():
    if len(sys.argv) < 2:
        print("usage: manual_search_test.py <vin_or_stock_number>")
        return 1
    query = sys.argv[1]

    saved_state = load_saved_state(STATE_FILE)

    with create_playwright_driver(headless=False, storage_state=saved_state) as driver:
        login_result = LoginTool().run(
            driver,
            username=os.environ["DEALER_CENTER_USERNAME"],
            password=os.environ["DEALER_CENTER_PASSWORD"],
            mfa_code_provider=terminal_mfa_code_provider,
        )
        print(f"[login] {login_result.status} reused_session={login_result.data.get('reused_session')}")

        # Re-save the session so next manual run can reuse it too —
        # creates .auth_state.json if it didn't exist yet, overwrites
        # it (with a fresh session) if it did.
        save_state(STATE_FILE, driver.save_session())

        # Catches everything, not just StepFailure — a raw Playwright
        # error here must not close the browser before you get a chance
        # to look at whatever page/modal it stopped on.
        try:
            search_result = (
                SearchInvoiceTool().run(driver, vin=None, stock_number=query)
                if len(query) < 10
                else SearchInvoiceTool().run(driver, vin=query, stock_number=None)
            )
            print(f"[search] SUCCESS match_type={search_result.data['match_type']}")
            print(f"[search] row_text={search_result.data['row_text']!r}")
        except StepFailure as exc:
            print(f"[search] FAILED: {exc.reason}")
            print("\n--- Dumping raw dropdown DOM for selector debugging ---")
            print(f"Rows found with current selector ({SEARCH_RESULT_ROW_SELECTOR!r}):",
                  driver.find_all_text(SEARCH_RESULT_ROW_SELECTOR))
            print("\nFull search box container text, for manually spotting the real selector:")
            print(driver.get_text("body"))
        except Exception as exc:
            print(f"[UNEXPECTED ERROR] {exc}")

        input("\nPress Enter to close the browser... ")

    return 0


if __name__ == "__main__":
    sys.exit(main())
