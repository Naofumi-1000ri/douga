"""Take screenshots of the Douga frontend for evaluation."""

import os
from pathlib import Path

from playwright.sync_api import sync_playwright

SCREENSHOT_DIR = Path("/Users/hgs/devel/douga/screenshots")
SCREENSHOT_DIR.mkdir(exist_ok=True)

FRONTEND_URL = "http://localhost:5173"


def take_screenshots():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="ja-JP",
        )
        page = context.new_page()

        # 1. Login page
        print("Taking screenshot: Login page")
        page.goto(f"{FRONTEND_URL}/login")
        page.wait_for_load_state("networkidle")
        page.screenshot(path=str(SCREENSHOT_DIR / "01_login.png"), full_page=True)

        # 2. Try to access dashboard (may redirect to login)
        print("Taking screenshot: Dashboard")
        page.goto(f"{FRONTEND_URL}/")
        page.wait_for_load_state("networkidle")
        page.screenshot(path=str(SCREENSHOT_DIR / "02_dashboard.png"), full_page=True)

        # 3. Editor page (if accessible)
        print("Taking screenshot: Editor")
        # Use a test project ID - this might show error or redirect
        page.goto(f"{FRONTEND_URL}/editor/test-project-123")
        page.wait_for_load_state("networkidle")
        page.screenshot(path=str(SCREENSHOT_DIR / "03_editor.png"), full_page=True)

        # 4. Check different viewport - mobile
        print("Taking screenshot: Mobile view")
        context2 = browser.new_context(
            viewport={"width": 375, "height": 812},  # iPhone X
            locale="ja-JP",
        )
        page2 = context2.new_page()
        page2.goto(f"{FRONTEND_URL}/login")
        page2.wait_for_load_state("networkidle")
        page2.screenshot(path=str(SCREENSHOT_DIR / "04_mobile_login.png"), full_page=True)

        context2.close()
        browser.close()

        print(f"\nScreenshots saved to: {SCREENSHOT_DIR}")
        for f in sorted(SCREENSHOT_DIR.glob("*.png")):
            print(f"  - {f.name}")


if __name__ == "__main__":
    take_screenshots()
