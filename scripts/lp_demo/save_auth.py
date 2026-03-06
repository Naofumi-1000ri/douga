"""Save authentication state for demo recording.

Opens a browser window, lets you log in manually via Firebase Google OAuth,
then saves the full auth state (cookies, localStorage, sessionStorage)
to auth_state.json for use by record_demo.py.
"""
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

DOUGA_URL = "https://douga-2f6f8.web.app"
AUTH_STATE_FILE = Path(__file__).parent / "auth_state.json"


async def main() -> None:
    print(f"[save_auth] Opening {DOUGA_URL} ...")
    print("[save_auth] A browser window will open. Please log in manually.")
    print()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
        )
        page = await context.new_page()

        await page.goto(DOUGA_URL)

        print("=" * 60)
        print("  1. Log in with your Google account in the browser.")
        print("  2. Wait until the dashboard fully loads.")
        print("  3. Come back here and press Enter.")
        print("=" * 60)
        print()

        # Wait for user confirmation
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: input("Press Enter after you have logged in... ")
        )

        # Save storage state (cookies + localStorage + sessionStorage)
        await context.storage_state(path=str(AUTH_STATE_FILE))
        print()
        print(f"[save_auth] Auth state saved to: {AUTH_STATE_FILE}")
        print("[save_auth] You can now run record_demo.py.")

        await context.close()
        await browser.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[save_auth] Cancelled.")
        sys.exit(1)
