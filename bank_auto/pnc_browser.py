"""
pnc_browser.py — PNC Bank transfer automation via browser session.

Maintains a cookie/session file to bypass 2FA on subsequent runs.
Provides APIs expected by main.py:
- transfer_external(...)
- transfer_internal(...)
"""

import os
import sys
import time
import pickle
import datetime
from pathlib import Path

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# ── Config ────────────────────────────────────────────────────────────────────

PNC_LOGIN_URL   = "https://www.onlinebanking.pnc.com/auth/login"
PNC_TRANSFER_URL = "https://www.onlinebanking.pnc.com/app/transfer-and-payments/transfer-money"
SESSION_FILE    = Path(__file__).parent / ".pnc_session.pkl"
WAIT_TIMEOUT    = 25  # seconds


# ── Driver Helper ─────────────────────────────────────────────────────────────

def _make_driver(headless: bool = False) -> uc.Chrome:
    opts = uc.ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1280,900")
    
    # Use persistent user data profile directory to keep browser trust tokens
    profile_dir = Path(__file__).parent / ".chrome_profile"
    profile_dir.mkdir(exist_ok=True)
    opts.add_argument(f"--user-data-dir={profile_dir}")
    
    # Bypass cloudflare/bot checks
    driver = uc.Chrome(options=opts)
    return driver


def _save_cookies(driver: uc.Chrome) -> None:
    cookies = driver.get_cookies()
    with open(SESSION_FILE, "wb") as f:
        pickle.dump(cookies, f)
    print(f"[+] Browser session cookies saved to {SESSION_FILE}")


def _load_cookies(driver: uc.Chrome, url: str) -> bool:
    if not SESSION_FILE.exists():
        return False
    driver.get(url)
    time.sleep(2)
    with open(SESSION_FILE, "rb") as f:
        cookies = pickle.load(f)
    for c in cookies:
        try:
            driver.add_cookie(c)
        except Exception:
            pass
    return True


def _is_logged_in(driver: uc.Chrome) -> bool:
    """Check if the user is logged in by inspecting the current URL/body."""
    url = driver.current_url
    if "/auth/" in url or "login" in url.lower():
        return False
    # If URL contains app pages or has log out button/dashboard keywords
    return "/app/" in url or "dashboard" in url.lower() or "accounts" in url.lower() or "summary" in url.lower()


def _login_flow(driver: uc.Chrome, username: str = "", password: str = "") -> bool:
    """Attempts cookie login first. If expired/fails, attempts credentials + manual 2FA."""
    print("[*] Loading cookies...")
    _load_cookies(driver, PNC_LOGIN_URL)
    driver.get(PNC_TRANSFER_URL)
    time.sleep(4)

    if _is_logged_in(driver):
        print("[+] Session loaded successfully from cookies (MFA Bypassed).")
        return True

    print("[!] Session cookies expired or not found. Navigating to login page...")
    driver.get(PNC_LOGIN_URL)
    time.sleep(4)

    # If already logged in on login page redirect
    if _is_logged_in(driver):
        _save_cookies(driver)
        return True

    # Attempt to fill in credentials
    if username and password:
        print(f"[*] Filling credentials for user: {username}")
        try:
            user_input = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[id*='user'], input[id*='userid'], input[name*='user'], input[type='text']"))
            )
            user_input.clear()
            user_input.send_keys(username)

            pass_input = driver.find_element(By.CSS_SELECTOR, "input[type='password'], input[id*='pass'], input[name*='pass']")
            pass_input.clear()
            pass_input.send_keys(password)

            # Click login
            login_btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit'], input[type='submit'], button[id*='login'], input[id*='login']")
            login_btn.click()
            time.sleep(3)
        except Exception as e:
            print(f"[!] Auto-fill failed: {e}. Please enter username and password manually in the browser.")

    print("\n[!] Please complete the login and 2FA process in the browser window.")
    print("[*] Once you are fully logged in and see your dashboard/accounts, return here.")
    
    # Wait until logged in
    start_time = time.time()
    while time.time() - start_time < 300:  # 5 minutes timeout
        if _is_logged_in(driver):
            print("[+] Login detected!")
            _save_cookies(driver)
            return True
        time.sleep(3)
    
    return False


# ── Interactive Setup Command ─────────────────────────────────────────────────

def cmd_setup():
    """Manual configuration command to save the session cookies."""
    print("[*] Launching browser for setup...")
    driver = _make_driver(headless=False)
    try:
        success = _login_flow(driver)
        if success:
            print("[+] Setup successful! Your session cookies are cached.")
        else:
            print("[red]Setup failed: login timeout.[/red]")
    finally:
        driver.quit()


# ── APIs for main.py ──────────────────────────────────────────────────────────

def transfer_external(username, password, from_account, to_routing, to_account, to_name, amount, memo):
    """
    Executes an external ACH transfer via PNC browser automation.
    API matching the signature called by main.py.
    """
    print(f"[*] Starting external transfer: ${amount:.2f} from {from_account} to {to_name} ({to_account})")
    driver = _make_driver(headless=False)
    try:
        if not _login_flow(driver, username, password):
            return {"status": "error", "message": "Failed to log in / authenticate session."}

        driver.get(PNC_TRANSFER_URL)
        time.sleep(5)

        # 1. Select source account
        try:
            from_dropdown = WebDriverWait(driver, WAIT_TIMEOUT).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "select[id*='from'], select[name*='from'], [data-testid*='from-account']"))
            )
            sel = Select(from_dropdown)
            matched = False
            for opt in sel.options:
                if from_account in opt.text or from_account in opt.get_attribute("value"):
                    sel.select_by_visible_text(opt.text)
                    matched = True
                    break
            if not matched:
                print(f"[!] Source account ending in {from_account} not found in dropdown.")
                input("Please select the source account manually in the browser, then press Enter here: ")
        except Exception as e:
            print(f"[!] Could not automate source account selection: {e}")
            input("Please select the source account manually in the browser, then press Enter here: ")

        time.sleep(1)

        # 2. Select destination account (must already be linked/added in PNC)
        try:
            to_dropdown = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "select[id*='to'], select[name*='to'], [data-testid*='to-account']"))
            )
            sel_to = Select(to_dropdown)
            matched = False
            # Try to match by destination account number or name
            for opt in sel_to.options:
                txt = opt.text.lower()
                val = opt.get_attribute("value").lower()
                if to_account in txt or to_account in val or to_name.lower() in txt:
                    sel_to.select_by_visible_text(opt.text)
                    matched = True
                    break
            if not matched:
                print(f"[!] Destination account {to_account} ({to_name}) not found in dropdown.")
                print(f"    Available options: {[o.text for o in sel_to.options]}")
                input("Please select the destination account manually in the browser, then press Enter here: ")
        except Exception as e:
            print(f"[!] Could not automate destination account selection: {e}")
            input("Please select the destination account manually in the browser, then press Enter here: ")

        time.sleep(1)

        # 3. Enter amount
        try:
            amt_field = driver.find_element(By.CSS_SELECTOR, "input[id*='amount'], input[name*='amount'], [data-testid*='amount']")
            amt_field.clear()
            amt_field.send_keys(f"{amount:.2f}")
        except Exception as e:
            print(f"[!] Could not automate amount entry: {e}")
            input("Please enter the amount manually in the browser, then press Enter here: ")

        # 4. Memo
        if memo:
            try:
                memo_field = driver.find_element(By.CSS_SELECTOR, "input[id*='memo'], input[name*='memo'], textarea[id*='memo']")
                memo_field.clear()
                memo_field.send_keys(memo)
            except Exception:
                pass

        time.sleep(1)

        # 5. Confirm and execute
        print("\n[!] Transfer is prepared in the browser.")
        print(f"    From Account: {from_account}")
        print(f"    To Account:   {to_account} ({to_name})")
        print(f"    Amount:       ${amount:.2f}")
        print("Please review and click the SUBMIT / TRANSFER button in the browser window to complete it.")
        
        input("\n[*] Once you have executed or cancelled the transfer in the browser, press Enter here to close: ")

        # Save cookies again in case they updated
        _save_cookies(driver)
        return {"status": "success", "srvrtid": "BROWSER-ACH", "message": "Browser transfer flow completed."}

    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        driver.quit()


def transfer_internal(username, password, src_account, to_account, amount, memo):
    """
    Executes an internal transfer via PNC browser automation.
    API matching the signature called by main.py.
    """
    print(f"[*] Starting internal transfer: ${amount:.2f} from {src_account} to {to_account}")
    driver = _make_driver(headless=False)
    try:
        if not _login_flow(driver, username, password):
            return {"status": "error", "message": "Failed to log in / authenticate session."}

        driver.get(PNC_TRANSFER_URL)
        time.sleep(5)

        # 1. Select source account
        try:
            from_dropdown = WebDriverWait(driver, WAIT_TIMEOUT).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "select[id*='from'], select[name*='from'], [data-testid*='from-account']"))
            )
            sel = Select(from_dropdown)
            matched = False
            for opt in sel.options:
                if src_account in opt.text or src_account in opt.get_attribute("value"):
                    sel.select_by_visible_text(opt.text)
                    matched = True
                    break
            if not matched:
                print(f"[!] Source account ending in {src_account} not found in dropdown.")
                input("Please select the source account manually, then press Enter here: ")
        except Exception as e:
            print(f"[!] Could not automate source account selection: {e}")
            input("Please select the source account manually, then press Enter here: ")

        time.sleep(1)

        # 2. Select destination account
        try:
            to_dropdown = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "select[id*='to'], select[name*='to'], [data-testid*='to-account']"))
            )
            sel_to = Select(to_dropdown)
            matched = False
            for opt in sel_to.options:
                if to_account in opt.text or to_account in opt.get_attribute("value"):
                    sel_to.select_by_visible_text(opt.text)
                    matched = True
                    break
            if not matched:
                print(f"[!] Destination account {to_account} not found in dropdown.")
                input("Please select the destination account manually, then press Enter here: ")
        except Exception as e:
            print(f"[!] Could not automate destination account selection: {e}")
            input("Please select the destination account manually, then press Enter here: ")

        time.sleep(1)

        # 3. Enter amount
        try:
            amt_field = driver.find_element(By.CSS_SELECTOR, "input[id*='amount'], input[name*='amount'], [data-testid*='amount']")
            amt_field.clear()
            amt_field.send_keys(f"{amount:.2f}")
        except Exception as e:
            print(f"[!] Could not automate amount entry: {e}")
            input("Please enter the amount manually, then press Enter here: ")

        # 4. Memo
        if memo:
            try:
                memo_field = driver.find_element(By.CSS_SELECTOR, "input[id*='memo'], input[name*='memo'], textarea[id*='memo']")
                memo_field.clear()
                memo_field.send_keys(memo)
            except Exception:
                pass

        time.sleep(1)

        # 5. Confirm and execute
        print("\n[!] Transfer is prepared in the browser.")
        print(f"    From Account: {src_account}")
        print(f"    To Account:   {to_account}")
        print(f"    Amount:       ${amount:.2f}")
        print("Please review and click the SUBMIT / TRANSFER button in the browser window to complete it.")
        
        input("\n[*] Once you have executed or cancelled the transfer in the browser, press Enter here to close: ")

        _save_cookies(driver)
        return {"status": "success", "srvrtid": "BROWSER-INTERNAL", "message": "Browser transfer flow completed."}

    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        driver.quit()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="PNC Browser transfer tool")
    parser.add_argument("cmd", choices=["setup"])
    args = parser.parse_args()
    if args.cmd == "setup":
        cmd_setup()
