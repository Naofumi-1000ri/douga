"""Record a demo video of the douga AI video editor for the LP/promo video.

This script automates a browser session that demonstrates:
  1. Opening the douga editor
  2. Opening the AI chat panel
  3. Typing an editing command (slowly, for visual effect)
  4. Waiting for the AI to respond and update the timeline
  5. Clicking Play to preview the result

The recording is saved as a .webm video via Playwright's built-in
record_video_dir. Screenshots are captured at key moments for use
as hero images on the landing page.

Usage:
    python record_demo.py --project-id <ID> --sequence-id <ID>
    python record_demo.py --project-id <ID> --sequence-id <ID> --command "BGMを追加して"
"""
import argparse
import asyncio
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright, Page, expect

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DOUGA_BASE_URL = "https://douga-2f6f8.web.app"
AUTH_STATE_FILE = Path(__file__).parent / "auth_state.json"
DEFAULT_COMMAND = "イントロ動画を作って"
TYPING_DELAY_MS = 80  # ms per character for slow-typing effect

# Output directories (relative to this script)
SCRIPT_DIR = Path(__file__).parent
OUTPUT_DIR = SCRIPT_DIR / "output"
SCREENSHOTS_DIR = OUTPUT_DIR / "screenshots"
VIDEOS_DIR = OUTPUT_DIR / "videos"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log(step: str, msg: str) -> None:
    print(f"[{timestamp()}] [{step}] {msg}")


async def slow_type(page: Page, selector: str, text: str, delay: int = TYPING_DELAY_MS) -> None:
    """Type text character-by-character with a delay for visual appeal."""
    element = page.locator(selector)
    await element.click()
    for char in text:
        await element.press_sequentially(char, delay=delay)


async def take_screenshot(page: Page, name: str) -> Path:
    """Take a full-page screenshot and save it with a descriptive name."""
    path = SCREENSHOTS_DIR / f"{name}.png"
    await page.screenshot(path=str(path), full_page=False)
    log("screenshot", f"Saved: {path}")
    return path


# ---------------------------------------------------------------------------
# Main choreography
# ---------------------------------------------------------------------------
async def record_demo(
    project_id: str,
    sequence_id: str,
    command_text: str = DEFAULT_COMMAND,
    headless: bool = False,
) -> None:
    """Run the full demo recording choreography."""

    if not AUTH_STATE_FILE.exists():
        print(f"ERROR: Auth state file not found: {AUTH_STATE_FILE}")
        print("Run save_auth.py first to create it.")
        sys.exit(1)

    # Prepare output directories
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

    # Use a temporary directory for Playwright's video recording, then copy
    video_tmp_dir = OUTPUT_DIR / "video_tmp"
    video_tmp_dir.mkdir(parents=True, exist_ok=True)

    editor_url = f"{DOUGA_BASE_URL}/project/{project_id}/sequence/{sequence_id}"
    log("init", f"Editor URL: {editor_url}")
    log("init", f"AI command:  {command_text}")
    log("init", f"Typing delay: {TYPING_DELAY_MS}ms/char")
    print()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            storage_state=str(AUTH_STATE_FILE),
            viewport={"width": 1920, "height": 1080},
            record_video_dir=str(video_tmp_dir),
            record_video_size={"width": 1920, "height": 1080},
        )
        page = await context.new_page()

        try:
            # ==============================================================
            # Phase 1: Page load (0-3s)
            # ==============================================================
            log("phase1", "Navigating to editor...")
            await page.goto(editor_url, wait_until="networkidle")
            log("phase1", "Page loaded. Waiting for editor to settle...")

            # Wait for the timeline area or a known element to appear
            await page.wait_for_selector('[class*="timeline"]', timeout=15000)
            await page.wait_for_timeout(2000)  # Let animations finish

            await take_screenshot(page, "01_editor_loaded")
            log("phase1", "Editor is ready.")

            # ==============================================================
            # Phase 2: Open AI chat panel (3-5s)
            # ==============================================================
            log("phase2", "Checking if AI chat panel is open...")

            # The AI button in the top bar contains text "AI"
            ai_button = page.locator('button:has-text("AI")').first

            # Check if the panel is already open by looking for the textarea
            chat_textarea = page.locator('textarea[placeholder="AIへの指示を入力..."]')
            is_panel_open = await chat_textarea.is_visible()

            if not is_panel_open:
                log("phase2", "AI panel is closed. Clicking AI button...")
                await ai_button.click()
                await page.wait_for_timeout(500)
                # Wait for textarea to appear
                await chat_textarea.wait_for(state="visible", timeout=5000)
                log("phase2", "AI panel opened.")
            else:
                log("phase2", "AI panel is already open.")

            await page.wait_for_timeout(1000)
            await take_screenshot(page, "02_ai_panel_open")

            # ==============================================================
            # Phase 3: Type AI command (5-8s)
            # ==============================================================
            log("phase3", f'Typing command: "{command_text}" ...')

            # Click the textarea to focus it
            await chat_textarea.click()
            await page.wait_for_timeout(300)

            # Type slowly for visual effect
            await chat_textarea.press_sequentially(command_text, delay=TYPING_DELAY_MS)
            log("phase3", "Finished typing.")

            await page.wait_for_timeout(500)
            await take_screenshot(page, "03_command_typed")

            # ==============================================================
            # Phase 4: Send command (8-9s)
            # ==============================================================
            log("phase4", "Pressing Enter to send command...")
            await chat_textarea.press("Enter")
            log("phase4", "Command sent. Waiting for AI response...")

            await page.wait_for_timeout(1000)

            # ==============================================================
            # Phase 5: Wait for AI response & timeline update (9-35s)
            # ==============================================================
            log("phase5", "Waiting for AI to process and update timeline...")

            # Watch for AI response messages to appear in the chat panel.
            # The AI response will add new message elements to the chat.
            # We'll poll for changes over ~25 seconds.
            max_wait_s = 26
            poll_interval_s = 2
            elapsed = 0
            screenshot_count = 0

            while elapsed < max_wait_s:
                await page.wait_for_timeout(poll_interval_s * 1000)
                elapsed += poll_interval_s

                # Take a screenshot every 8 seconds during the wait
                if elapsed % 8 == 0 or elapsed >= max_wait_s:
                    screenshot_count += 1
                    await take_screenshot(page, f"04_ai_progress_{screenshot_count:02d}")

                # Check if the AI is still loading (look for loading indicators)
                # The send button is disabled while loading; when it re-enables,
                # the AI has finished responding.
                send_button = page.locator(
                    'button:has(svg) >> nth=-1'
                ).locator('xpath=//button[contains(@class, "bg-primary-600")]').first

                # Alternative: check if any loading spinner is gone
                loading_indicator = page.locator('[class*="animate-spin"], [class*="loading"]')
                is_loading = await loading_indicator.count() > 0

                if not is_loading and elapsed > 5:
                    log("phase5", f"AI appears to have finished after ~{elapsed}s.")
                    break

                log("phase5", f"  ... waiting ({elapsed}s / {max_wait_s}s)")

            await take_screenshot(page, "05_ai_response_complete")
            log("phase5", "AI response phase complete.")

            # ==============================================================
            # Phase 6: Click Play to preview (35-40s)
            # ==============================================================
            log("phase6", "Looking for Play button...")

            # The play button has title="再生" and is inside the timeline controls
            play_button = page.locator('button[title="再生"], button[title="一時停止"]').first

            if await play_button.is_visible():
                log("phase6", "Clicking Play button...")
                await play_button.click()
                await take_screenshot(page, "06_playing")
                log("phase6", "Playback started.")

                # ==============================================================
                # Phase 7: Let preview play (40-45s)
                # ==============================================================
                log("phase7", "Letting preview play for 5 seconds...")
                await page.wait_for_timeout(5000)
                await take_screenshot(page, "07_preview_playing")

                # Stop playback
                pause_button = page.locator('button[title="一時停止"]').first
                if await pause_button.is_visible():
                    await pause_button.click()
                    log("phase7", "Playback paused.")
            else:
                log("phase6", "Play button not found or not visible. Skipping playback.")
                await page.wait_for_timeout(3000)

            await take_screenshot(page, "08_final")
            log("done", "Demo recording choreography complete!")

        except Exception as e:
            log("error", f"An error occurred: {e}")
            await take_screenshot(page, "99_error")
            raise

        finally:
            # Close context to finalize video recording
            await context.close()
            await browser.close()

    # ------------------------------------------------------------------
    # Copy recorded video to output dir with a descriptive name
    # ------------------------------------------------------------------
    video_files = list(video_tmp_dir.glob("*.webm"))
    if video_files:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = VIDEOS_DIR / f"demo_{ts}.webm"
        shutil.copy2(video_files[0], dest)
        log("output", f"Video saved: {dest}")

        # Clean up temp dir
        shutil.rmtree(video_tmp_dir, ignore_errors=True)
    else:
        log("output", "WARNING: No video file found. Check Playwright video settings.")

    # Summary
    print()
    print("=" * 60)
    print("  Recording complete!")
    print(f"  Screenshots: {SCREENSHOTS_DIR}")
    print(f"  Videos:      {VIDEOS_DIR}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record a demo video of the douga AI video editor.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
    python record_demo.py --project-id abc123 --sequence-id seq456
    python record_demo.py --project-id abc123 --sequence-id seq456 --command "BGMを追加して"
    python record_demo.py --project-id abc123 --sequence-id seq456 --headless

Environment variables (used as defaults):
    DOUGA_PROJECT_ID    - Project ID
    DOUGA_SEQUENCE_ID   - Sequence ID
    DOUGA_DEMO_COMMAND  - AI command text
""",
    )
    parser.add_argument(
        "--project-id",
        default=os.environ.get("DOUGA_PROJECT_ID"),
        help="Project ID (or set DOUGA_PROJECT_ID env var)",
    )
    parser.add_argument(
        "--sequence-id",
        default=os.environ.get("DOUGA_SEQUENCE_ID"),
        help="Sequence ID (or set DOUGA_SEQUENCE_ID env var)",
    )
    parser.add_argument(
        "--command",
        default=os.environ.get("DOUGA_DEMO_COMMAND", DEFAULT_COMMAND),
        help=f'AI command to type (default: "{DEFAULT_COMMAND}")',
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=False,
        help="Run in headless mode (no visible browser window)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not args.project_id:
        print("ERROR: --project-id is required (or set DOUGA_PROJECT_ID env var)")
        sys.exit(1)
    if not args.sequence_id:
        print("ERROR: --sequence-id is required (or set DOUGA_SEQUENCE_ID env var)")
        sys.exit(1)

    asyncio.run(
        record_demo(
            project_id=args.project_id,
            sequence_id=args.sequence_id,
            command_text=args.command,
            headless=args.headless,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[record_demo] Cancelled.")
        sys.exit(1)
