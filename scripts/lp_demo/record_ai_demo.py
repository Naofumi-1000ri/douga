"""Record an AI editing demo video via Playwright with auto Firebase auth.

Records the browser session as the AI assistant edits a video timeline.
Uses high-frequency screenshots + FFmpeg for high-quality output.

Outputs:
  - High-quality MP4 from screenshot sequence (primary)
  - Playwright .webm recording (backup)
  - Key-moment screenshots

Usage:
    python record_ai_demo.py
    python record_ai_demo.py --command "クリップを前に詰めて"
    python record_ai_demo.py --headless
"""
import argparse
import asyncio
import base64
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DOUGA_BASE_URL = "https://douga-2f6f8.web.app"
FIREBASE_API_KEY = "AIzaSyDdiewtsCtucW9qah_umsABzO9IrrahKOs"
SA_EMAIL = "firebase-adminsdk-fbsvc@douga-2f6f8.iam.gserviceaccount.com"
FIREBASE_UID = "nXyjrW6anrPY2qAi4rtOhpPO5Kt1"

DEFAULT_PROJECT_ID = "0ef3004f-6dd0-4a1a-a5ea-25bb3d548777"
DEFAULT_SEQUENCE_ID = "8b01b709-145e-421f-84f9-4472355bcf30"
DEFAULT_COMMAND = "クリップを前に詰めて"

SCRIPT_DIR = Path(__file__).parent
OUTPUT_DIR = SCRIPT_DIR / "output"
SCREENSHOTS_DIR = OUTPUT_DIR / "demo_screenshots"
VIDEOS_DIR = OUTPUT_DIR / "demo_videos"
FRAMES_DIR = OUTPUT_DIR / "frames"

TYPING_DELAY_MS = 100  # ms per char for slow-typing
FRAME_INTERVAL_MS = 500  # screenshot interval (0.5s)


# ---------------------------------------------------------------------------
# Firebase Auth Bridge (IndexedDB injection)
# ---------------------------------------------------------------------------
def get_firebase_tokens() -> dict:
    """Get Firebase ID token + refresh token via custom token flow."""
    access_token = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    now = int(time.time())
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "RS256", "typ": "JWT"}).encode()
    ).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        json.dumps({
            "iss": SA_EMAIL, "sub": SA_EMAIL,
            "aud": "https://identitytoolkit.googleapis.com/google.identity.identitytoolkit.v1.IdentityToolkit",
            "iat": now, "exp": now + 3600, "uid": FIREBASE_UID,
        }).encode()
    ).rstrip(b"=").decode()
    signing_input = f"{header}.{payload}"

    resp = httpx.post(
        f"https://iam.googleapis.com/v1/projects/-/serviceAccounts/{SA_EMAIL}:signBlob",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"bytesToSign": base64.b64encode(signing_input.encode()).decode()},
    )
    resp.raise_for_status()
    sig = resp.json()["signature"].replace("+", "-").replace("/", "_").rstrip("=")
    custom_token = f"{signing_input}.{sig}"

    # Exchange custom token for ID token + refresh token
    resp2 = httpx.post(
        f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken?key={FIREBASE_API_KEY}",
        json={"token": custom_token, "returnSecureToken": True},
    )
    resp2.raise_for_status()
    data = resp2.json()
    return {
        "id_token": data["idToken"],
        "refresh_token": data["refreshToken"],
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def log(step: str, msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [{step}] {msg}")


async def screenshot(page, name: str) -> Path:
    """Save a named screenshot (key moments)."""
    path = SCREENSHOTS_DIR / f"{name}.png"
    await page.screenshot(path=str(path))
    log("screenshot", f"{path.name}")
    return path


class FrameCapture:
    """Manages sequential frame capture for high-quality video generation."""

    def __init__(self, frames_dir: Path):
        self.frames_dir = frames_dir
        self.frame_count = 0

    def setup(self) -> None:
        """Clear and create frames directory."""
        if self.frames_dir.exists():
            shutil.rmtree(self.frames_dir)
        self.frames_dir.mkdir(parents=True, exist_ok=True)

    async def capture(self, page, label: str = "") -> Path:
        """Capture a single frame as a sequential PNG."""
        self.frame_count += 1
        path = self.frames_dir / f"frame_{self.frame_count:04d}.png"
        await page.screenshot(path=str(path))
        if label:
            log("frame", f"#{self.frame_count:04d} {label}")
        return path

    async def capture_duration(self, page, duration_s: float, interval_s: float = 0.5, label: str = "") -> int:
        """Capture frames over a duration at a given interval. Returns number of frames captured."""
        count = 0
        elapsed = 0.0
        while elapsed < duration_s:
            await self.capture(page, label=f"{label} ({elapsed:.1f}s)" if label else "")
            count += 1
            await page.wait_for_timeout(int(interval_s * 1000))
            elapsed += interval_s
        return count


def build_hq_video(frames_dir: Path, output_path: Path, input_fps: int = 2) -> bool:
    """Build high-quality MP4 from sequential frame PNGs using FFmpeg.

    Args:
        frames_dir: Directory containing frame_NNNN.png files
        output_path: Output MP4 path
        input_fps: Input framerate (how long each frame is displayed)
    """
    frame_pattern = str(frames_dir / "frame_%04d.png")

    # Check we have frames
    frame_files = sorted(frames_dir.glob("frame_*.png"))
    if not frame_files:
        log("ffmpeg", "No frames found!")
        return False

    log("ffmpeg", f"Building video from {len(frame_files)} frames (input fps={input_fps})...")

    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(input_fps),
        "-i", frame_pattern,
        "-c:v", "libx264",
        "-preset", "slow",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-vf", "fps=30",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log("ffmpeg", f"FFmpeg error: {result.stderr[-500:]}")
        return False

    if output_path.exists():
        size_mb = output_path.stat().st_size / 1024 / 1024
        log("ffmpeg", f"HQ video: {output_path} ({size_mb:.1f}MB)")
        return True
    return False


# ---------------------------------------------------------------------------
# Main Recording
# ---------------------------------------------------------------------------
async def record_demo(
    project_id: str,
    sequence_id: str,
    command_text: str,
    headless: bool = False,
) -> None:
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    video_tmp = OUTPUT_DIR / "video_tmp"
    video_tmp.mkdir(parents=True, exist_ok=True)

    editor_url = f"{DOUGA_BASE_URL}/project/{project_id}/sequence/{sequence_id}"

    # Frame capture setup
    fc = FrameCapture(FRAMES_DIR)
    fc.setup()

    # Auth
    log("auth", "Getting Firebase tokens...")
    tokens = get_firebase_tokens()
    log("auth", "Tokens ready.")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            record_video_dir=str(video_tmp),
            record_video_size={"width": 1920, "height": 1080},
        )
        page = await context.new_page()

        try:
            # ── Phase 1: Auth via IndexedDB + Load ────────────────
            # Use addInitScript to inject auth into IndexedDB BEFORE app scripts run
            id_token = tokens["id_token"]
            refresh_token = tokens["refresh_token"]
            init_script = f"""
            (async () => {{
                try {{
                    const key = 'firebase:authUser:{FIREBASE_API_KEY}:[DEFAULT]';
                    const userObj = {{
                        uid: '{FIREBASE_UID}',
                        email: 'naofumi@1000ri.jp',
                        emailVerified: true,
                        displayName: 'Naofumi',
                        isAnonymous: false,
                        providerData: [{{ providerId: 'google.com', uid: '{FIREBASE_UID}', displayName: 'Naofumi', email: 'naofumi@1000ri.jp' }}],
                        stsTokenManager: {{ refreshToken: '{refresh_token}', accessToken: '{id_token}', expirationTime: Date.now() + 3600000 }},
                        createdAt: '1700000000000',
                        lastLoginAt: String(Date.now()),
                        apiKey: '{FIREBASE_API_KEY}',
                        appName: '[DEFAULT]',
                    }};
                    const request = indexedDB.open('firebaseLocalStorageDb', 1);
                    request.onupgradeneeded = (e) => {{
                        const db = e.target.result;
                        if (!db.objectStoreNames.contains('firebaseLocalStorage')) {{
                            db.createObjectStore('firebaseLocalStorage');
                        }}
                    }};
                    request.onsuccess = (e) => {{
                        const db = e.target.result;
                        const tx = db.transaction('firebaseLocalStorage', 'readwrite');
                        const store = tx.objectStore('firebaseLocalStorage');
                        store.put({{ fbase_key: key, value: userObj }}, key);
                    }};
                }} catch (e) {{ /* silent */ }}
            }})();
            """
            await context.add_init_script(init_script)
            log("phase1", "Init script added. Navigating to editor...")

            await page.goto(editor_url, wait_until="load", timeout=60000)

            # Wait for editor to stabilize (3 seconds, capturing frames)
            log("phase1", "Waiting for editor to stabilize...")
            await fc.capture_duration(page, duration_s=3.0, interval_s=0.5, label="loading")

            # Extra wait for full render
            await page.wait_for_timeout(3000)
            await fc.capture_duration(page, duration_s=3.0, interval_s=0.5, label="stabilize")

            # Check if we're on the editor (not login page)
            current_url = page.url
            log("phase1", f"URL: {current_url}")
            if "/project/" not in current_url:
                log("phase1", "Still on login page, auth may have failed")
            await screenshot(page, "01_loaded")
            await fc.capture(page, "editor_loaded")

            # ── Phase 2: Open AI Panel + Click textarea ─────────
            log("phase2", "Opening AI panel...")
            textarea = page.locator('textarea[placeholder*="AI"]')
            if not await textarea.is_visible():
                ai_btn = page.locator('button:has-text("AI")').first
                if await ai_btn.is_visible():
                    await ai_btn.click()
                    await page.wait_for_timeout(1000)
                    await fc.capture(page, "ai_btn_clicked")

            await page.wait_for_timeout(500)
            await fc.capture(page, "ai_panel_open")

            # Click on textarea
            textarea = page.locator('textarea[placeholder*="AI"]')
            if await textarea.is_visible():
                await textarea.click()
                await page.wait_for_timeout(500)
                await fc.capture(page, "textarea_focused")

            await screenshot(page, "02_ai_panel")

            # ── Phase 3: Type AI Command (with per-char frames) ──
            log("phase3", f'Typing: "{command_text}"')
            textarea = page.locator('textarea[placeholder*="AI"]')
            if await textarea.is_visible():
                # Type character by character, capturing frame after each
                for i, char in enumerate(command_text):
                    await textarea.type(char, delay=0)
                    await page.wait_for_timeout(TYPING_DELAY_MS)
                    # Capture every character for smooth typing animation
                    await fc.capture(page, f"typing_char_{i+1}")

                await page.wait_for_timeout(500)
                await fc.capture(page, "typing_complete")
                await screenshot(page, "03_typed")

                # ── Phase 4: Send Command ─────────────────────────
                log("phase4", "Sending command (Enter)...")
                await fc.capture(page, "before_send")
                await textarea.press("Enter")
                await page.wait_for_timeout(500)
                await fc.capture(page, "after_send")
                await page.wait_for_timeout(1000)
                await fc.capture(page, "sent_waiting")
                await screenshot(page, "04_sent")

                # ── Phase 5: Wait for AI Response (1s interval) ───
                log("phase5", "Waiting for AI response...")
                max_wait = 60
                elapsed = 0
                responded = False
                while elapsed < max_wait:
                    await page.wait_for_timeout(1000)
                    elapsed += 1
                    await fc.capture(page, f"waiting_{elapsed}s")

                    # Check for completion indicators
                    ok_text = page.locator('text=/\\[OK\\]/')
                    error_text = page.locator('text=/\\[ERROR\\]/')
                    # Also check for streamed response content (AI message bubbles)
                    ai_msg = page.locator('[class*="ai-message"], [class*="assistant"], [class*="response"]')
                    fail_text = page.locator('text=/失敗/')
                    if await ok_text.count() > 0 or await error_text.count() > 0 or await fail_text.count() > 0 or (elapsed >= 10 and await ai_msg.count() > 0):
                        log("phase5", f"AI responded after ~{elapsed}s")
                        responded = True
                        break
                    log("phase5", f"  ... {elapsed}s / {max_wait}s")

                if not responded:
                    log("phase5", f"Timeout after {max_wait}s (may still be processing)")

                await page.wait_for_timeout(500)
                await fc.capture(page, "response_received")
                await screenshot(page, "05_response")

                # ── Phase 6: Show Result (3s capture) ─────────────
                log("phase6", "Capturing final result for 3s...")
                await fc.capture_duration(page, duration_s=3.0, interval_s=0.5, label="final")
                await screenshot(page, "06_final")
            else:
                log("phase3", "AI textarea not found, capturing current state...")
                await fc.capture_duration(page, duration_s=3.0, interval_s=0.5, label="no_textarea")
                await screenshot(page, "03_no_textarea")

        except Exception as e:
            log("error", str(e))
            await screenshot(page, "99_error")
            raise
        finally:
            await context.close()
            await browser.close()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # ── Build HQ video from frames ──────────────────────────────
    hq_mp4 = VIDEOS_DIR / f"ai_demo_hq_{ts}.mp4"
    build_hq_video(FRAMES_DIR, hq_mp4, input_fps=2)

    # ── Copy Playwright video (backup) ──────────────────────────
    webm_files = list(video_tmp.glob("*.webm"))
    if webm_files:
        dest = VIDEOS_DIR / f"ai_demo_{ts}.webm"
        shutil.copy2(webm_files[0], dest)
        log("output", f"Playwright video: {dest}")

        # Convert to MP4
        pw_mp4 = VIDEOS_DIR / f"ai_demo_pw_{ts}.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-i", str(dest),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            str(pw_mp4),
        ], capture_output=True)
        if pw_mp4.exists():
            size_mb = pw_mp4.stat().st_size / 1024 / 1024
            log("output", f"Playwright MP4:  {pw_mp4} ({size_mb:.1f}MB)")

        shutil.rmtree(video_tmp, ignore_errors=True)
    else:
        log("output", "WARNING: No Playwright video file found")
        shutil.rmtree(video_tmp, ignore_errors=True)

    # ── Summary ─────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("Recording Complete")
    print("=" * 60)
    print(f"  Frames captured: {fc.frame_count}")
    print(f"  Frames dir:      {FRAMES_DIR}")
    print(f"  Screenshots:     {SCREENSHOTS_DIR}")
    print(f"  Videos:          {VIDEOS_DIR}")
    if hq_mp4.exists():
        size_mb = hq_mp4.stat().st_size / 1024 / 1024
        print(f"  HQ Video:        {hq_mp4} ({size_mb:.1f}MB)")
    print("=" * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Record AI editing demo")
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    parser.add_argument("--sequence-id", default=DEFAULT_SEQUENCE_ID)
    parser.add_argument("--command", default=DEFAULT_COMMAND)
    parser.add_argument("--headless", action="store_true", default=False)
    args = parser.parse_args()

    asyncio.run(record_demo(
        project_id=args.project_id,
        sequence_id=args.sequence_id,
        command_text=args.command,
        headless=args.headless,
    ))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(1)
