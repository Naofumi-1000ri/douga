"""Record a "Making Of" demo: AI builds a video from scratch.

Shows the full workflow:
  1. Open empty project (English UI)
  2. AI command: "Build a tutorial video with these assets"
  3. Watch timeline get populated
  4. AI command: "Add fade transitions between clips"
  5. Final result

Usage:
    python record_making_demo.py
    python record_making_demo.py --headless
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

PROJECT_ID = "59d581be-1c4d-4f16-b4ce-de52afacabd1"

# AI commands to execute in sequence
AI_COMMANDS = [
    "Build a 30 second promo video using the uploaded assets. Use nebula backgrounds and add BGM. Reply in English.",
]

SCRIPT_DIR = Path(__file__).parent
OUTPUT_DIR = SCRIPT_DIR / "output"
SCREENSHOTS_DIR = OUTPUT_DIR / "making_screenshots"
VIDEOS_DIR = OUTPUT_DIR / "demo_videos"
FRAMES_DIR = OUTPUT_DIR / "making_frames"

TYPING_DELAY_MS = 60   # faster typing for English
FRAME_INTERVAL_MS = 500


# ---------------------------------------------------------------------------
# Firebase Auth Bridge
# ---------------------------------------------------------------------------
def get_firebase_tokens() -> dict:
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

    resp2 = httpx.post(
        f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken?key={FIREBASE_API_KEY}",
        json={"token": custom_token, "returnSecureToken": True},
    )
    resp2.raise_for_status()
    data = resp2.json()
    return {"id_token": data["idToken"], "refresh_token": data["refreshToken"]}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def log(step: str, msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [{step}] {msg}")


async def screenshot(page, name: str) -> Path:
    path = SCREENSHOTS_DIR / f"{name}.png"
    await page.screenshot(path=str(path))
    log("screenshot", f"{path.name}")
    return path


class FrameCapture:
    def __init__(self, frames_dir: Path):
        self.frames_dir = frames_dir
        self.frame_count = 0

    def setup(self) -> None:
        if self.frames_dir.exists():
            shutil.rmtree(self.frames_dir)
        self.frames_dir.mkdir(parents=True, exist_ok=True)

    async def capture(self, page, label: str = "") -> Path:
        self.frame_count += 1
        path = self.frames_dir / f"frame_{self.frame_count:04d}.png"
        await page.screenshot(path=str(path))
        if label:
            log("frame", f"#{self.frame_count:04d} {label}")
        return path

    async def capture_duration(self, page, duration_s: float, interval_s: float = 0.5, label: str = "") -> int:
        count = 0
        elapsed = 0.0
        while elapsed < duration_s:
            await self.capture(page, label=f"{label} ({elapsed:.1f}s)" if label else "")
            count += 1
            await page.wait_for_timeout(int(interval_s * 1000))
            elapsed += interval_s
        return count


def build_hq_video(frames_dir: Path, output_path: Path, input_fps: int = 2) -> bool:
    frame_files = sorted(frames_dir.glob("frame_*.png"))
    if not frame_files:
        log("ffmpeg", "No frames found!")
        return False

    log("ffmpeg", f"Building video from {len(frame_files)} frames (input fps={input_fps})...")
    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(input_fps),
        "-i", str(frames_dir / "frame_%04d.png"),
        "-c:v", "libx264", "-preset", "slow", "-crf", "18",
        "-pix_fmt", "yuv420p", "-vf", "fps=30",
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
# AI Command Helper
# ---------------------------------------------------------------------------
async def send_ai_command(page, fc: FrameCapture, command: str, cmd_index: int) -> bool:
    """Type and send an AI command, wait for response. Returns True if successful."""
    log(f"cmd{cmd_index}", f'Sending: "{command}"')

    # Find textarea (English: "Enter instructions for AI...", Japanese: "AIへの指示...")
    textarea = page.locator(
        'textarea[placeholder*="instructions"], '
        'textarea[placeholder*="AI"], '
        'textarea[placeholder*="指示"]'
    )
    if not await textarea.is_visible():
        log(f"cmd{cmd_index}", "Textarea not visible, attempting to open AI panel...")
        ai_btn = page.locator('button:has-text("AI")').first
        if await ai_btn.is_visible():
            await ai_btn.click()
            await page.wait_for_timeout(1000)
        if not await textarea.is_visible():
            log(f"cmd{cmd_index}", "Textarea still not found!")
            return False

    await textarea.click()
    await page.wait_for_timeout(300)

    # Type command with per-character capture
    for i, char in enumerate(command):
        await textarea.type(char, delay=0)
        await page.wait_for_timeout(TYPING_DELAY_MS)
        # Capture every 3 chars for smoother but not excessive frames
        if i % 3 == 0:
            await fc.capture(page, f"cmd{cmd_index}_typing")

    await fc.capture(page, f"cmd{cmd_index}_typed")
    await screenshot(page, f"cmd{cmd_index}_typed")
    await page.wait_for_timeout(300)

    # Send
    await textarea.press("Enter")
    await fc.capture(page, f"cmd{cmd_index}_sent")
    await page.wait_for_timeout(1000)

    # Wait for response
    max_wait = 90
    elapsed = 0
    responded = False
    while elapsed < max_wait:
        await page.wait_for_timeout(1000)
        elapsed += 1
        await fc.capture(page, f"cmd{cmd_index}_wait_{elapsed}s")

        # Check completion
        ok_text = page.locator('text=/\\[OK\\]/')
        error_text = page.locator('text=/\\[ERROR\\]/')
        fail_text = page.locator('text=/失敗|Failed/')
        ai_msg = page.locator('[class*="ai-message"], [class*="assistant"], [class*="response"]')

        if await ok_text.count() > 0 or await error_text.count() > 0 or await fail_text.count() > 0:
            log(f"cmd{cmd_index}", f"AI responded after ~{elapsed}s")
            responded = True
            break
        if elapsed >= 15 and await ai_msg.count() > 0:
            # Check if content has stabilized (streamed response done)
            log(f"cmd{cmd_index}", f"AI response detected at ~{elapsed}s, waiting for completion...")
            await page.wait_for_timeout(3000)
            await fc.capture(page, f"cmd{cmd_index}_response_settling")
            responded = True
            break

        log(f"cmd{cmd_index}", f"  ... {elapsed}s / {max_wait}s")

    if not responded:
        log(f"cmd{cmd_index}", f"Timeout after {max_wait}s")

    # Capture result
    await fc.capture_duration(page, duration_s=2.0, interval_s=0.5, label=f"cmd{cmd_index}_result")
    await screenshot(page, f"cmd{cmd_index}_result")
    return responded


# ---------------------------------------------------------------------------
# Main Recording
# ---------------------------------------------------------------------------
async def record_demo(headless: bool = False) -> None:
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    video_tmp = OUTPUT_DIR / "video_tmp_making"
    video_tmp.mkdir(parents=True, exist_ok=True)

    # Get default sequence for the project
    API = "https://douga-api-344056413972.asia-northeast1.run.app/api"

    fc = FrameCapture(FRAMES_DIR)
    fc.setup()

    # Auth
    log("auth", "Getting Firebase tokens...")
    tokens = get_firebase_tokens()
    log("auth", "Tokens ready.")

    # Get default sequence ID
    log("setup", "Getting default sequence...")
    resp = httpx.get(
        f"{API}/projects/{PROJECT_ID}/sequences/default",
        headers={"Authorization": f"Bearer {tokens['id_token']}"},
    )
    if resp.status_code == 200:
        seq_id = resp.json()["id"]
    else:
        # Create one
        resp = httpx.post(
            f"{API}/projects/{PROJECT_ID}/sequences",
            headers={
                "Authorization": f"Bearer {tokens['id_token']}",
                "Content-Type": "application/json",
            },
            json={"name": "Main"},
        )
        seq_id = resp.json()["id"]
    log("setup", f"Sequence: {seq_id}")

    editor_url = f"{DOUGA_BASE_URL}/project/{PROJECT_ID}/sequence/{seq_id}"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            record_video_dir=str(video_tmp),
            record_video_size={"width": 1920, "height": 1080},
            locale="en-US",  # Set browser locale to English
        )
        page = await context.new_page()

        try:
            # ── Phase 1: Auth + Load + Set English ────────────────
            id_token = tokens["id_token"]
            refresh_token = tokens["refresh_token"]
            init_script = f"""
            (async () => {{
                try {{
                    // Set language to English
                    localStorage.setItem('i18nextLng', 'en');
                    localStorage.setItem('language', 'en');

                    // Firebase auth injection
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
            log("phase1", "Init script added (English locale + auth). Navigating...")

            await page.goto(editor_url, wait_until="load", timeout=60000)

            # Wait for editor
            log("phase1", "Waiting for editor to load...")
            await page.wait_for_timeout(3000)
            await fc.capture_duration(page, duration_s=3.0, interval_s=0.5, label="loading")

            # Extra stabilization
            await page.wait_for_timeout(2000)
            await fc.capture_duration(page, duration_s=2.0, interval_s=0.5, label="stabilize")

            current_url = page.url
            log("phase1", f"URL: {current_url}")
            await screenshot(page, "01_editor_loaded")
            await fc.capture(page, "editor_ready")

            # ── Phase 2: Open AI Panel ────────────────────────────
            log("phase2", "Checking AI panel state...")
            # Check if textarea is already visible (panel already open)
            textarea = page.locator(
                'textarea[placeholder*="instructions"], '
                'textarea[placeholder*="AI"], '
                'textarea[placeholder*="指示"]'
            )
            if await textarea.is_visible():
                log("phase2", "AI panel already open — skipping button click")
            else:
                log("phase2", "AI panel closed, clicking AI button to open...")
                ai_btn = page.locator('button:has-text("AI")').first
                if await ai_btn.is_visible():
                    await ai_btn.click()
                    await page.wait_for_timeout(1000)
                    await fc.capture(page, "ai_panel_opened")
                else:
                    log("phase2", "WARNING: AI button not found!")

            await fc.capture_duration(page, duration_s=1.0, interval_s=0.5, label="panel_ready")
            await screenshot(page, "02_ai_panel")

            # ── Phase 3: Execute AI Commands ──────────────────────
            for i, command in enumerate(AI_COMMANDS):
                success = await send_ai_command(page, fc, command, i + 1)
                if success:
                    log(f"cmd{i+1}", "Command completed successfully")
                else:
                    log(f"cmd{i+1}", "Command may have timed out")

                # Pause between commands
                await fc.capture_duration(page, duration_s=2.0, interval_s=0.5, label=f"after_cmd{i+1}")

            # ── Phase 4: Sync timeline & Refresh ──────────────────
            log("phase4", "Syncing project timeline to sequence...")

            # The AI agent writes clips to project's timeline_data but
            # the sequence (shown in UI) may still be empty due to locking.
            # Fetch the project, copy its timeline_data to the sequence.
            import httpx as _httpx
            auth_header = {"Authorization": f"Bearer {tokens['id_token']}"}

            proj_resp = _httpx.get(f"{API}/projects/{PROJECT_ID}", headers=auth_header)
            if proj_resp.status_code == 200:
                proj_tl = proj_resp.json().get("timeline_data", {})
                proj_clips_count = sum(
                    len(layer.get("clips", [])) for layer in proj_tl.get("layers", [])
                ) + sum(
                    len(track.get("clips", [])) for track in proj_tl.get("audio_tracks", [])
                )
                log("phase4", f"Project has {proj_clips_count} clips total")

                if proj_clips_count > 0:
                    # Unlock sequence first (may be locked by browser session)
                    _httpx.post(
                        f"{API}/projects/{PROJECT_ID}/sequences/{seq_id}/unlock",
                        headers=auth_header,
                    )
                    await page.wait_for_timeout(500)

                    # Get current sequence version
                    seq_resp = _httpx.get(
                        f"{API}/projects/{PROJECT_ID}/sequences/{seq_id}",
                        headers=auth_header,
                    )
                    seq_ver = seq_resp.json().get("version", 1) if seq_resp.status_code == 200 else 1

                    # Push project timeline_data to sequence
                    put_resp = _httpx.put(
                        f"{API}/projects/{PROJECT_ID}/sequences/{seq_id}",
                        headers={**auth_header, "Content-Type": "application/json"},
                        json={"timeline_data": proj_tl, "version": seq_ver},
                    )
                    if put_resp.status_code == 200:
                        log("phase4", "Timeline synced to sequence successfully")
                    else:
                        log("phase4", f"Sync failed: {put_resp.status_code} {put_resp.text[:200]}")

            # Reload to show updated timeline
            log("phase4", "Reloading page...")
            await page.reload(wait_until="load")
            await page.wait_for_timeout(4000)
            await fc.capture_duration(page, duration_s=2.0, interval_s=0.5, label="reload")
            await screenshot(page, "03_after_reload")

            # Click "Fit" button to zoom timeline to show all clips
            fit_btn = page.locator('button:has-text("Fit")').first
            if await fit_btn.is_visible():
                log("phase4", "Clicking Fit button...")
                await fit_btn.click()
                await page.wait_for_timeout(1000)
                await fc.capture(page, "timeline_fit")
            else:
                log("phase4", "Fit button not found")

            await fc.capture_duration(page, duration_s=2.0, interval_s=0.5, label="timeline_view")
            await screenshot(page, "04_timeline_fitted")

            # Try clicking the play button briefly to show preview
            play_btn = page.locator('button[aria-label*="Play"], button[aria-label*="play"]').first
            if not await play_btn.is_visible():
                # Try finding the play icon button (triangle icon)
                play_btn = page.locator('svg >> xpath=../..').filter(has=page.locator('path[d*="M"]')).first
            # Fallback: look for the big play button in the preview
            play_circle = page.locator('button:near(:text("0:00"))').first
            if await play_circle.is_visible():
                log("phase4", "Clicking play...")
                await play_circle.click()
                await page.wait_for_timeout(500)
                await fc.capture_duration(page, duration_s=3.0, interval_s=0.5, label="playback")
                # Pause after a few seconds
                await play_circle.click()
                await page.wait_for_timeout(300)

            await screenshot(page, "05_preview")

            # Final captures
            log("phase4", "Capturing final state...")
            await fc.capture_duration(page, duration_s=2.0, interval_s=0.5, label="final")
            await screenshot(page, "99_final")

        except Exception as e:
            log("error", str(e))
            await screenshot(page, "99_error")
            raise
        finally:
            await context.close()
            await browser.close()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Build HQ video from frames
    hq_mp4 = VIDEOS_DIR / f"making_demo_hq_{ts}.mp4"
    build_hq_video(FRAMES_DIR, hq_mp4, input_fps=2)

    # Copy Playwright video
    webm_files = list(video_tmp.glob("*.webm"))
    if webm_files:
        dest = VIDEOS_DIR / f"making_demo_{ts}.webm"
        shutil.copy2(webm_files[0], dest)
        log("output", f"Playwright video: {dest}")

        pw_mp4 = VIDEOS_DIR / f"making_demo_pw_{ts}.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-i", str(dest),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p", str(pw_mp4),
        ], capture_output=True)
        if pw_mp4.exists():
            size_mb = pw_mp4.stat().st_size / 1024 / 1024
            log("output", f"Playwright MP4:  {pw_mp4} ({size_mb:.1f}MB)")

        shutil.rmtree(video_tmp, ignore_errors=True)
    else:
        log("output", "WARNING: No Playwright video file found")
        shutil.rmtree(video_tmp, ignore_errors=True)

    # Summary
    print()
    print("=" * 60)
    print("  Making Demo Recording Complete")
    print("=" * 60)
    print(f"  Project:    {PROJECT_ID}")
    print(f"  Sequence:   {seq_id}")
    print(f"  Commands:   {len(AI_COMMANDS)}")
    print(f"  Frames:     {fc.frame_count}")
    print(f"  Screenshots:{SCREENSHOTS_DIR}")
    print(f"  Videos:     {VIDEOS_DIR}")
    if hq_mp4.exists():
        size_mb = hq_mp4.stat().st_size / 1024 / 1024
        print(f"  HQ Video:   {hq_mp4} ({size_mb:.1f}MB)")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="Record making-of demo (English)")
    parser.add_argument("--headless", action="store_true", default=False)
    args = parser.parse_args()
    asyncio.run(record_demo(headless=args.headless))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(1)
