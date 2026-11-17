#!/usr/bin/env python3
"""
POC: DealerCenter browser automation feasibility check.

Flow:
  1. Reuse a saved session (storage_state) if we have one and it's still valid.
  2. Otherwise, log in with credentials from .env (Auth0-hosted login at
     auth.dealercenter.net: #username, #password, submit "Continue").
  3. Detect an MFA/OTP challenge (code emailed to the user). We cannot read
     that email, so we prompt for the code in THIS TERMINAL, type it into
     the page ourselves, and submit.
  4. Dismiss the post-login notification modal if one appears.
  5. Save the session for next time.
  6. Run one search (VIN last-6 or PO) in the top search box and print
     whatever Inventory/Deal matches show up. No clicks into a vehicle,
     no form edits, no saves.

Usage:
  python poc_dealercenter_login.py <vin_last6_or_po>
"""
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
import os
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

BASE_URL = "https://app.dealercenter.net"
STATE_FILE = Path(__file__).parent / ".auth_state.json"

load_dotenv()
USERNAME = os.environ.get("DEALER_CENTER_USERNAME")
PASSWORD = os.environ.get("DEALER_CENTER_PASSWORD")


def looks_logged_in(page) -> bool:
    """Being off the auth host is the real proof login worked. The search-box
    check is just a bonus confirmation — it must NOT be able to override a
    real login into a false 'failed', or we throw away a working session
    (and its remember-device cookie) before ever saving it."""
    if "auth.dealercenter.net" in page.url:
        return False
    try:
        page.wait_for_selector(
            "input[type='search'], input[placeholder*='Search' i]",
            timeout=15000,
        )
    except PWTimeoutError:
        print("[!] Off the auth domain (login succeeded) but didn't find the "
              "search box within 15s — selector guess may be stale, treating "
              "login as successful anyway. Current URL:", page.url)
    return True


MFA_CODE_INPUT_SELECTORS = (
    "input[name='code'], input[autocomplete='one-time-code'], "
    "input[inputmode='numeric'], input[type='tel']"
)
# DealerCenter's dashboard is built on Kendo UI Angular (.k-dialog,
# .k-overlay, .k-window — confirmed from a live error's DOM dump), not
# plain ARIA dialogs. Close-button selectors need to cover both.
MODAL_CLOSE_SELECTORS = (
    "[role='dialog'] button[aria-label='Close'], "
    "[role='dialog'] button:has-text('Got it'), "
    "[role='dialog'] button:has-text('Dismiss'), "
    "[role='dialog'] button:has-text('OK'), "
    "[role='dialog'] .close, [role='dialog'] [class*='close' i], "
    ".k-dialog-close, .k-dialog-titlebar-actions button, "
    ".k-window-titlebar .k-button, button[aria-label='Close'], "
    "button[title='Close']"
)
MODAL_CONTAINER_SELECTOR = "[role='dialog'], .k-dialog, .k-window"


def submit_button_for(field_locator):
    """Auth0's Universal Login puts TWO button[type=submit] inside the SAME
    form: a decorative/hidden one (aria-hidden="true") before the fields,
    and the real visible one after them. Scoping to the right <form> isn't
    enough on its own — we also have to exclude the aria-hidden one, or
    `.first` grabs the decoy and clicking it fails with
    'intercepts pointer events' (it sits behind the visible form)."""
    return field_locator.locator(
        "xpath=ancestor::form[1]//button[@type='submit'][not(@aria-hidden='true')]"
    ).last


def click_when_enabled(btn, timeout_ms=10000):
    """Auth0's submit buttons stay disabled until client-side validation
    finishes; clicking a beat too early is a no-op. Poll until enabled."""
    btn.wait_for(timeout=timeout_ms)
    deadline = time.monotonic() + (timeout_ms / 1000)
    while time.monotonic() < deadline:
        if btn.is_enabled():
            btn.click()
            return
        time.sleep(0.2)
    btn.click()  # last attempt even if we never saw "enabled"


def looks_like_mfa(page) -> bool:
    # Still on auth.dealercenter.net but the username/password fields are
    # gone (replaced by a code-entry screen).
    if "auth.dealercenter.net" in page.url:
        try:
            if page.locator("#password").first.is_visible(timeout=500):
                return False
        except Exception:
            pass
        return True
    mfa_hints = [
        "text=/verification code/i",
        "text=/one-time/i",
        "text=/two-factor/i",
        "text=/enter code/i",
        "input[autocomplete='one-time-code']",
    ]
    for sel in mfa_hints:
        try:
            if page.locator(sel).first.is_visible(timeout=1000):
                return True
        except Exception:
            continue
    return False


def handle_mfa(page):
    print("\n[!] MFA/OTP challenge detected (code emailed to you).")

    code_field = page.locator(MFA_CODE_INPUT_SELECTORS).first
    try:
        code_field.wait_for(timeout=10000)
    except PWTimeoutError:
        print("[!] Could not find an MFA code input field with the current selectors.")
        print("    Current URL:", page.url)
        return False

    while True:
        code = input("    Enter the 6-digit code here in the terminal: ").strip()
        if code.isdigit() and len(code) == 6:
            break
        print("    That's not a 6-digit code, try again (check for typos/spaces).")

    code_field.fill(code)
    click_when_enabled(submit_button_for(code_field))
    page.wait_for_load_state("domcontentloaded")
    time.sleep(2)

    if looks_like_mfa(page):
        print("[!] DealerCenter rejected that code (still on the challenge screen).")
        retry = input("    Try another code? [y/N]: ").strip().lower()
        if retry == "y":
            return handle_mfa(page)
        return False
    return True


def wait_visible(locator, timeout_ms=2000) -> bool:
    """is_visible() does NOT wait — it's a single immediate DOM check, not
    an actionability poll. Kendo/Angular modals render asynchronously
    (there's a busy-loader spinner first), so an is_visible() check fired
    right after navigation can run before the modal exists and silently
    report 'nothing here' a split second before it appears. wait_for(state=
    'visible') actually polls, which is what we need for a race like this."""
    try:
        locator.wait_for(state="visible", timeout=timeout_ms)
        return True
    except Exception:
        return False


def accept_trust_browser_modal(page) -> bool:
    """'Trust this browser?' (QR Code Sign-In) shows up on the dashboard
    right after login. Always accept it — that's the whole point of
    automating this. Returns True if it was handled (so callers can loop
    for a second stacked modal)."""
    trust_btn = page.locator("button:has-text('trust this browser')").first
    if not wait_visible(trust_btn, 2500):
        return False
    print("[*] 'Trust this browser?' modal detected, accepting")
    trust_btn.click()
    page.wait_for_timeout(1000)
    return True


def dismiss_blocking_modals(page, max_rounds=5):
    """DealerCenter can chain several post-login modals (Trust this
    browser?, Account Security phone-verification nag, maybe others we
    haven't seen yet). Clear all of them before returning — a single
    unrecognized modal leaves a Kendo `.k-overlay` on top of the page that
    blocks every click underneath it, which is what hung and crashed
    do_search() twice already. We never fill out a modal's form (e.g. the
    phone number field) — only close/accept, nothing else."""
    for _ in range(max_rounds):
        if accept_trust_browser_modal(page):
            continue

        modal = page.locator(MODAL_CONTAINER_SELECTOR).first
        if not wait_visible(modal, 2500):
            break

        print("[*] Modal detected, attempting to close it")
        closed = False
        close_btn = page.locator(MODAL_CLOSE_SELECTORS).first
        if wait_visible(close_btn, 1500):
            try:
                close_btn.click(timeout=2000)
                closed = True
            except Exception:
                pass

        if not closed:
            print("    No known close button matched, pressing Escape")
            page.keyboard.press("Escape")

        page.wait_for_timeout(800)

    # Final safety net: don't hand back control to the caller while a
    # leftover overlay can still eat clicks on whatever comes next.
    try:
        page.locator(".k-overlay").first.wait_for(state="hidden", timeout=5000)
    except Exception:
        pass


def do_login(page):
    print(f"[*] Navigating to {BASE_URL}")
    page.goto(BASE_URL, wait_until="domcontentloaded")

    print("[*] Filling credentials")
    user_field = page.locator("#username")
    pass_field = page.locator("#password")

    user_field.wait_for(timeout=15000)
    user_field.fill(USERNAME)
    pass_field.fill(PASSWORD)

    click_when_enabled(submit_button_for(pass_field))

    page.wait_for_load_state("domcontentloaded")
    time.sleep(2)

    if looks_like_mfa(page):
        if not handle_mfa(page):
            return False

    if not looks_logged_in(page):
        print("[!] Login does not look successful. Current URL:", page.url)
        return False

    print("[+] Login successful.")
    dismiss_blocking_modals(page)
    return True


def do_search(page, query: str):
    # Belt-and-suspenders: a modal can render late (it already has, twice),
    # so sweep for one again right before we need to click anything.
    dismiss_blocking_modals(page)

    print(f"[*] Running search for: {query}")
    search_box = page.locator(
        "input[type='search'], input[placeholder*='Search' i]"
    ).first
    search_box.click()
    search_box.fill(query)
    page.wait_for_timeout(1500)  # let the dropdown populate

    dropdown = page.locator("[role='listbox'], .search-results, .dropdown-menu").first
    try:
        dropdown.wait_for(timeout=5000)
        text = dropdown.inner_text()
        print("[+] Search dropdown contents:\n")
        print(text)
    except PWTimeoutError:
        print("[!] No dropdown appeared within 5s — selector may need adjusting,")
        print("    or there were no matches for this query.")


def main():
    if len(sys.argv) < 2:
        print("usage: python poc_dealercenter_login.py <vin_last6_or_po>")
        return 1
    query = sys.argv[1]

    if not USERNAME or not PASSWORD:
        print("[!] DEALER_CENTER_USERNAME / DEALER_CENTER_PASSWORD missing from .env")
        return 1

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)

        if STATE_FILE.exists():
            print("[*] Found saved session, trying to reuse it")
            context = browser.new_context(storage_state=str(STATE_FILE))
        else:
            context = browser.new_context()

        page = context.new_page()

        if STATE_FILE.exists():
            page.goto(BASE_URL, wait_until="domcontentloaded")
            if not looks_logged_in(page):
                print("[*] Saved session expired, logging in fresh")
                if not do_login(page):
                    browser.close()
                    return 1
            else:
                dismiss_blocking_modals(page)
        else:
            if not do_login(page):
                browser.close()
                return 1

        context.storage_state(path=str(STATE_FILE))
        print(f"[*] Session saved to {STATE_FILE}")

        do_search(page, query)

        input("\nPress Enter to close the browser... ")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
