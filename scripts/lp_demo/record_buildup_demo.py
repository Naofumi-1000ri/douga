"""Record a 'buildup' demo: clips appear progressively on the timeline.

Uses DOM manipulation in Playwright to hide all clips, then reveal them
layer-by-layer from bottom (background) to top (text) + audio, creating
a satisfying "video being assembled" visual.

Outputs:
  - High-quality MP4 from frame sequence
  - Key-moment screenshots

Usage:
    python record_buildup_demo.py
    python record_buildup_demo.py --headless
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

SCRIPT_DIR = Path(__file__).parent
OUTPUT_DIR = SCRIPT_DIR / "output"
SCREENSHOTS_DIR = OUTPUT_DIR / "buildup_screenshots"
VIDEOS_DIR = OUTPUT_DIR / "demo_videos"
FRAMES_DIR = OUTPUT_DIR / "buildup_frames"

FRAME_INTERVAL_MS = 250  # 4 fps capture for smooth animation


# ---------------------------------------------------------------------------
# Firebase Auth (same as record_ai_demo.py)
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

    async def capture_duration(self, page, duration_s: float, interval_s: float = 0.25, label: str = "") -> int:
        count = 0
        elapsed = 0.0
        while elapsed < duration_s:
            await self.capture(page, label=f"{label} ({elapsed:.1f}s)" if label else "")
            count += 1
            await page.wait_for_timeout(int(interval_s * 1000))
            elapsed += interval_s
        return count


def build_hq_video(frames_dir: Path, output_path: Path, input_fps: int = 4) -> bool:
    frame_pattern = str(frames_dir / "frame_%04d.png")
    frame_files = sorted(frames_dir.glob("frame_*.png"))
    if not frame_files:
        log("ffmpeg", "No frames found!")
        return False

    log("ffmpeg", f"Building video from {len(frame_files)} frames (input fps={input_fps})...")
    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(input_fps),
        "-i", frame_pattern,
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
# JavaScript: clip discovery and manipulation
# ---------------------------------------------------------------------------

# Discover all clip elements and tag them with data-buildup attributes.
# Returns { layers: [ { name, clipCount, trackType } ], totalClips }
JS_DISCOVER_CLIPS = """
() => {
    const allClips = [];

    // Find clip elements: absolutely positioned with left+width+cursor inside relative containers
    const candidates = document.querySelectorAll('div[class*="absolute"][class*="rounded"][class*="select-none"]');
    candidates.forEach(el => {
        const style = el.style;
        if (style.left && style.width && style.cursor) {
            const parent = el.parentElement;
            if (parent && parent.classList.contains('relative')) {
                allClips.push(el);
            }
        }
    });

    // Group clips by parent (layer/track)
    const layerMap = new Map();
    allClips.forEach(clip => {
        const parent = clip.parentElement;
        if (!layerMap.has(parent)) {
            layerMap.set(parent, []);
        }
        layerMap.get(parent).push(clip);
    });

    // Build layer list with audio/video classification
    // Audio tracks have h-16 class (fixed 64px height), video layers have dynamic height via style
    const layers = [];
    let clipIdx = 0;

    layerMap.forEach((clips, parentEl) => {
        const isAudio = parentEl.classList.contains('h-16');
        const layerInfo = {
            clipCount: clips.length,
            domIndex: layers.length,
            isAudio: isAudio,
        };

        clips.forEach(clip => {
            clip.setAttribute('data-buildup-idx', String(clipIdx));
            clip.setAttribute('data-buildup-layer', String(layers.length));
            clipIdx++;
        });

        layers.push(layerInfo);
    });

    return { layers, totalClips: clipIdx };
}
"""

# Hide all clips (opacity 0, with transition for smooth reveal)
JS_HIDE_ALL_CLIPS = """
() => {
    const clips = document.querySelectorAll('[data-buildup-idx]');
    clips.forEach(clip => {
        clip.style.transition = 'opacity 0.4s ease-in-out';
        clip.style.opacity = '0';
    });
    return clips.length;
}
"""

# Reveal clips for a specific layer (by data-buildup-layer value)
JS_REVEAL_LAYER = """
(layerIdx) => {
    const clips = document.querySelectorAll(`[data-buildup-layer="${layerIdx}"]`);
    clips.forEach(clip => {
        clip.style.opacity = '1';
    });
    return clips.length;
}
"""

# Reveal a single clip by its buildup index
JS_REVEAL_CLIP = """
(clipIdx) => {
    const clip = document.querySelector(`[data-buildup-idx="${clipIdx}"]`);
    if (clip) {
        clip.style.opacity = '1';
        return true;
    }
    return false;
}
"""

# Reveal all clips at once
JS_REVEAL_ALL = """
() => {
    const clips = document.querySelectorAll('[data-buildup-idx]');
    clips.forEach(clip => {
        clip.style.opacity = '1';
    });
    return clips.length;
}
"""

# Get clips grouped by layer with their positions for ordering
JS_GET_CLIP_DETAILS = """
() => {
    const result = [];
    const clips = document.querySelectorAll('[data-buildup-idx]');
    clips.forEach(clip => {
        result.push({
            idx: parseInt(clip.getAttribute('data-buildup-idx')),
            layer: parseInt(clip.getAttribute('data-buildup-layer')),
            left: parseFloat(clip.style.left) || 0,
            width: parseFloat(clip.style.width) || 0,
        });
    });
    return result;
}
"""


# ---------------------------------------------------------------------------
# Main Recording
# ---------------------------------------------------------------------------
async def record_buildup(
    project_id: str,
    sequence_id: str,
    headless: bool = False,
) -> None:
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

    editor_url = f"{DOUGA_BASE_URL}/project/{project_id}/sequence/{sequence_id}"

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
        )
        page = await context.new_page()

        try:
            # ── Phase 1: Auth + Load ──────────────────────────
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
            log("phase1", "Page loaded, waiting for editor to stabilize...")
            await page.wait_for_timeout(6000)

            current_url = page.url
            log("phase1", f"URL: {current_url}")
            if "/project/" not in current_url:
                log("phase1", "WARNING: May not be on editor page")

            # ── Phase 2: Discover clips ───────────────────────
            log("phase2", "Discovering timeline clips...")
            info = await page.evaluate(JS_DISCOVER_CLIPS)
            total_clips = info["totalClips"]
            layers = info["layers"]
            log("phase2", f"Found {total_clips} clips across {len(layers)} layers/tracks")
            for i, layer in enumerate(layers):
                log("phase2", f"  Layer {i}: {layer['clipCount']} clips")

            if total_clips == 0:
                log("phase2", "ERROR: No clips found! Taking debug screenshot...")
                await page.screenshot(path=str(SCREENSHOTS_DIR / "debug_no_clips.png"))
                await context.close()
                await browser.close()
                return

            # Get clip details for ordering
            clip_details = await page.evaluate(JS_GET_CLIP_DETAILS)

            # ── Phase 3: Hide all clips ───────────────────────
            log("phase3", "Hiding all clips...")
            hidden = await page.evaluate(JS_HIDE_ALL_CLIPS)
            log("phase3", f"Hidden {hidden} clips")
            await page.wait_for_timeout(500)

            # Capture "empty timeline" state
            log("phase3", "Capturing empty timeline...")
            await page.screenshot(path=str(SCREENSHOTS_DIR / "00_empty.png"))
            await fc.capture_duration(page, duration_s=1.5, interval_s=0.25, label="empty")

            # ── Phase 4: Progressive reveal ───────────────────
            # Reveal order: bottom video layers first (background → content → text),
            # then audio tracks last.
            # In DOM, video layers are top-to-bottom, so reverse for buildup.

            layers_with_clips = [(i, l) for i, l in enumerate(layers) if l["clipCount"] > 0]

            # Separate video layers and audio tracks using isAudio flag from JS
            video_layers = []
            audio_layers = []
            for idx, layer in layers_with_clips:
                if layer.get("isAudio", False):
                    audio_layers.append((idx, layer))
                else:
                    video_layers.append((idx, layer))

            log("phase4", f"Video layers: {len(video_layers)}, Audio tracks: {len(audio_layers)}")

            # Build-up order: background (last video) → middle → top → audio
            reveal_order = list(reversed(video_layers)) + audio_layers
            log("phase4", f"Reveal order: {[i for i, _ in reveal_order]}")

            for step, (layer_idx, layer) in enumerate(reveal_order):
                clip_count = layer["clipCount"]
                log("phase4", f"Step {step+1}/{len(reveal_order)}: "
                    f"Layer {layer_idx} ({clip_count} clips)")

                # Get clips for this layer, sorted by left position
                layer_clips = sorted(
                    [c for c in clip_details if c["layer"] == layer_idx],
                    key=lambda c: c["left"],
                )

                if clip_count <= 3:
                    # Few clips: reveal one by one with pause
                    for clip in layer_clips:
                        await page.evaluate(JS_REVEAL_CLIP, clip["idx"])
                        await page.wait_for_timeout(300)
                        await fc.capture(page, f"layer{layer_idx}_clip{clip['idx']}")
                    # Hold for a moment
                    await fc.capture_duration(page, duration_s=0.75, interval_s=0.25,
                                              label=f"layer{layer_idx}_hold")
                else:
                    # Many clips: reveal in rapid bursts (2-3 at a time)
                    batch_size = max(1, clip_count // 3)
                    for i in range(0, len(layer_clips), batch_size):
                        batch = layer_clips[i:i + batch_size]
                        for clip in batch:
                            await page.evaluate(JS_REVEAL_CLIP, clip["idx"])
                        await page.wait_for_timeout(200)
                        await fc.capture(page, f"layer{layer_idx}_batch{i}")
                    # Hold
                    await fc.capture_duration(page, duration_s=0.75, interval_s=0.25,
                                              label=f"layer{layer_idx}_hold")

                # Screenshot at each layer completion
                await page.screenshot(
                    path=str(SCREENSHOTS_DIR / f"0{step+1}_layer{layer_idx}.png")
                )

            # ── Phase 5: Final state ──────────────────────────
            log("phase5", "All clips revealed. Capturing final state...")
            await page.evaluate(JS_REVEAL_ALL)  # Ensure everything is visible
            await page.wait_for_timeout(500)
            await fc.capture_duration(page, duration_s=2.0, interval_s=0.25, label="final")
            await page.screenshot(path=str(SCREENSHOTS_DIR / "99_final.png"))

        except Exception as e:
            log("error", str(e))
            import traceback
            traceback.print_exc()
            await page.screenshot(path=str(SCREENSHOTS_DIR / "99_error.png"))
        finally:
            await context.close()
            await browser.close()

    # ── Build video ──────────────────────────────────────────
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    hq_mp4 = VIDEOS_DIR / f"buildup_demo_{ts}.mp4"
    build_hq_video(FRAMES_DIR, hq_mp4, input_fps=4)

    # ── Summary ──────────────────────────────────────────────
    print()
    print("=" * 60)
    print("Buildup Recording Complete")
    print("=" * 60)
    print(f"  Frames captured: {fc.frame_count}")
    print(f"  Frames dir:      {FRAMES_DIR}")
    print(f"  Screenshots:     {SCREENSHOTS_DIR}")
    if hq_mp4.exists():
        size_mb = hq_mp4.stat().st_size / 1024 / 1024
        dur = fc.frame_count / 4  # at 4 fps input
        print(f"  Video:           {hq_mp4} ({size_mb:.1f}MB, ~{dur:.0f}s)")
    print("=" * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Record buildup demo")
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    parser.add_argument("--sequence-id", default=DEFAULT_SEQUENCE_ID)
    parser.add_argument("--headless", action="store_true", default=False)
    args = parser.parse_args()

    asyncio.run(record_buildup(
        project_id=args.project_id,
        sequence_id=args.sequence_id,
        headless=args.headless,
    ))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(1)
