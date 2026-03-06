"""Take high-quality LP screenshots via Playwright with programmatic Firebase auth.

Usage:
    python screenshot_lp.py
    python screenshot_lp.py --project-id <ID> --sequence-id <ID>
    python screenshot_lp.py --headless
"""
import argparse
import asyncio
import base64
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DOUGA_BASE_URL = "https://douga-2f6f8.web.app"
FIREBASE_API_KEY = "AIzaSyDdiewtsCtucW9qah_umsABzO9IrrahKOs"
FIREBASE_PROJECT_ID = "douga-2f6f8"
SA_EMAIL = "firebase-adminsdk-fbsvc@douga-2f6f8.iam.gserviceaccount.com"
FIREBASE_UID = "nXyjrW6anrPY2qAi4rtOhpPO5Kt1"

# Default project to screenshot (Kaori's project with real content)
DEFAULT_PROJECT_ID = "0ef3004f-6dd0-4a1a-a5ea-25bb3d548777"
DEFAULT_SEQUENCE_ID = "8b01b709-145e-421f-84f9-4472355bcf30"

SCRIPT_DIR = Path(__file__).parent
OUTPUT_DIR = SCRIPT_DIR / "output" / "lp_screenshots"


# ---------------------------------------------------------------------------
# Firebase Auth Bridge
# ---------------------------------------------------------------------------
def get_gcloud_access_token() -> str:
    """Get gcloud access token for IAM API calls."""
    result = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def create_custom_token(access_token: str) -> str:
    """Create a Firebase custom token via IAM signBlob."""
    now = int(time.time())
    exp = now + 3600

    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "RS256", "typ": "JWT"}).encode()
    ).rstrip(b"=").decode()

    payload = base64.urlsafe_b64encode(
        json.dumps({
            "iss": SA_EMAIL,
            "sub": SA_EMAIL,
            "aud": "https://identitytoolkit.googleapis.com/google.identity.identitytoolkit.v1.IdentityToolkit",
            "iat": now,
            "exp": exp,
            "uid": FIREBASE_UID,
        }).encode()
    ).rstrip(b"=").decode()

    signing_input = f"{header}.{payload}"

    # Sign via IAM signBlob
    resp = httpx.post(
        f"https://iam.googleapis.com/v1/projects/-/serviceAccounts/{SA_EMAIL}:signBlob",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"bytesToSign": base64.b64encode(signing_input.encode()).decode()},
    )
    resp.raise_for_status()
    signed_blob = resp.json()["signature"]

    # Convert from standard base64 to URL-safe
    signature = signed_blob.replace("+", "-").replace("/", "_").rstrip("=")

    return f"{signing_input}.{signature}"


def exchange_for_id_token(custom_token: str) -> dict:
    """Exchange custom token for Firebase ID token + refresh token."""
    resp = httpx.post(
        f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken?key={FIREBASE_API_KEY}",
        json={"token": custom_token, "returnSecureToken": True},
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "id_token": data["idToken"],
        "refresh_token": data["refreshToken"],
        "local_id": data.get("localId", FIREBASE_UID),
        "expires_in": data.get("expiresIn", "3600"),
    }


def get_firebase_auth() -> dict:
    """Full auth flow: gcloud → custom token → ID token."""
    print("[auth] Getting gcloud access token...")
    access_token = get_gcloud_access_token()
    print("[auth] Creating Firebase custom token...")
    custom_token = create_custom_token(access_token)
    print("[auth] Exchanging for ID token...")
    auth_data = exchange_for_id_token(custom_token)
    auth_data["custom_token"] = custom_token
    print(f"[auth] Authenticated as UID: {auth_data['local_id']}")
    return auth_data


# ---------------------------------------------------------------------------
# Playwright Screenshot Session
# ---------------------------------------------------------------------------
async def inject_firebase_auth(page, custom_token: str) -> None:
    """Sign in using Firebase SDK already loaded in the app."""
    # The douga app uses Firebase Auth - call signInWithCustomToken via the SDK
    result = await page.evaluate(f"""
    async () => {{
        // Wait for Firebase to be available
        let attempts = 0;
        while (!window.__FIREBASE_AUTH__ && attempts < 20) {{
            await new Promise(r => setTimeout(r, 500));
            attempts++;
            // Try to find Firebase auth instance from various global locations
            if (typeof firebase !== 'undefined' && firebase.auth) {{
                window.__FIREBASE_AUTH__ = firebase.auth();
            }}
        }}

        // Try using the Firebase modular SDK (v9+) which stores auth in internal modules
        // The app bundles Firebase, so we need to find the auth instance
        // Most React apps expose it through the module system

        // Approach: Import Firebase dynamically and sign in
        try {{
            const {{ getAuth, signInWithCustomToken }} = await import('https://www.gstatic.com/firebasejs/10.7.1/firebase-auth.js');
            const {{ initializeApp, getApps }} = await import('https://www.gstatic.com/firebasejs/10.7.1/firebase-app.js');

            let app;
            const apps = getApps();
            if (apps.length > 0) {{
                app = apps[0];
            }} else {{
                app = initializeApp({{
                    apiKey: '{FIREBASE_API_KEY}',
                    authDomain: 'douga-2f6f8.firebaseapp.com',
                    projectId: 'douga-2f6f8',
                }});
            }}
            const auth = getAuth(app);
            const cred = await signInWithCustomToken(auth, '{custom_token}');
            return {{ success: true, uid: cred.user.uid }};
        }} catch (e) {{
            return {{ success: false, error: e.message }};
        }}
    }}
    """)
    return result


async def take_screenshots(
    project_id: str,
    sequence_id: str,
    headless: bool = False,
) -> list[Path]:
    """Take LP screenshots with Playwright."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    saved_files: list[Path] = []

    # Get Firebase auth
    auth_data = get_firebase_auth()

    editor_url = f"{DOUGA_BASE_URL}/project/{project_id}/sequence/{sequence_id}"
    print(f"\n[playwright] URL: {editor_url}")
    print(f"[playwright] Headless: {headless}")
    print(f"[playwright] Output: {OUTPUT_DIR}\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            device_scale_factor=1,
        )
        page = await context.new_page()

        # Step 1: Navigate to app and sign in with Firebase custom token
        print("[step1] Navigating to app...")
        await page.goto(editor_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

        # Step 2: Sign in with custom token via Firebase SDK in the browser
        print("[step2] Signing in with Firebase custom token...")
        auth_result = await inject_firebase_auth(page, auth_data["custom_token"])
        print(f"[step2] Auth result: {auth_result}")

        if auth_result and auth_result.get("success"):
            # Reload to pick up the authenticated state
            print("[step3] Reloading with auth...")
            await page.goto(editor_url, wait_until="load", timeout=60000)
            await page.wait_for_timeout(5000)
        else:
            # Fallback: try setting the ID token directly in localStorage
            print("[step2] Firebase SDK auth failed, trying localStorage fallback...")
            await page.evaluate(f"""
            () => {{
                localStorage.setItem('douga_auth_token', '{auth_data["id_token"]}');
            }}
            """)
            await page.goto(editor_url, wait_until="load", timeout=60000)
            await page.wait_for_timeout(5000)

        current_url = page.url
        print(f"[step3] Current URL: {current_url}")

        # Step 4: Wait for editor to fully load
        print("[step4] Waiting for editor to settle...")
        try:
            await page.wait_for_selector('[class*="timeline"], [class*="Timeline"]', timeout=15000)
        except Exception:
            print("[step4] Timeline selector not found, waiting longer...")
        await page.wait_for_timeout(3000)

        # ============================================================
        # Screenshot 1: Full editor view
        # ============================================================
        print("[screenshot] 01_editor_full - Full editor view")
        path = OUTPUT_DIR / "01_editor_full.png"
        await page.screenshot(path=str(path))
        saved_files.append(path)
        print(f"  Saved: {path}")

        # ============================================================
        # Screenshot 2: Click on a clip to show properties
        # ============================================================
        print("[screenshot] 02_with_properties - Editor with clip selected")
        # Try clicking on a timeline clip
        clip_el = page.locator('[class*="clip"], [data-clip-id]').first
        try:
            if await clip_el.is_visible(timeout=3000):
                await clip_el.click()
                await page.wait_for_timeout(1000)
        except Exception:
            pass
        path = OUTPUT_DIR / "02_with_properties.png"
        await page.screenshot(path=str(path))
        saved_files.append(path)
        print(f"  Saved: {path}")

        # ============================================================
        # Screenshot 3: AI panel with conversation
        # ============================================================
        print("[screenshot] 03_ai_panel - AI assistant panel")
        # Check if AI button exists and click it
        ai_button = page.locator('button:has-text("AI")').first
        try:
            if await ai_button.is_visible(timeout=3000):
                ai_panel_textarea = page.locator('textarea[placeholder*="AI"]')
                if not await ai_panel_textarea.is_visible():
                    await ai_button.click()
                    await page.wait_for_timeout(1000)
        except Exception:
            pass
        path = OUTPUT_DIR / "03_ai_panel.png"
        await page.screenshot(path=str(path))
        saved_files.append(path)
        print(f"  Saved: {path}")

        # ============================================================
        # Screenshot 4: Type an AI command (for visual)
        # ============================================================
        print("[screenshot] 04_ai_command - AI command typed")
        textarea = page.locator('textarea[placeholder*="AI"]')
        try:
            if await textarea.is_visible(timeout=3000):
                await textarea.click()
                await textarea.press_sequentially("BGMのボリュームを下げて", delay=60)
                await page.wait_for_timeout(500)
        except Exception:
            pass
        path = OUTPUT_DIR / "04_ai_command.png"
        await page.screenshot(path=str(path))
        saved_files.append(path)
        print(f"  Saved: {path}")

        # ============================================================
        # Screenshot 5: Zoomed preview area
        # ============================================================
        print("[screenshot] 05_preview_area - Zoomed preview")
        preview_el = page.locator('video, [class*="preview"], [class*="Preview"]').first
        try:
            if await preview_el.is_visible(timeout=3000):
                box = await preview_el.bounding_box()
                if box:
                    # Take a cropped screenshot of just the preview area with padding
                    pad = 20
                    path = OUTPUT_DIR / "05_preview_area.png"
                    await page.screenshot(
                        path=str(path),
                        clip={
                            "x": max(0, box["x"] - pad),
                            "y": max(0, box["y"] - pad),
                            "width": box["width"] + pad * 2,
                            "height": box["height"] + pad * 2,
                        },
                    )
                    saved_files.append(path)
                    print(f"  Saved: {path}")
        except Exception as e:
            print(f"  Preview screenshot skipped: {e}")

        # ============================================================
        # Screenshot 6: Timeline zoomed
        # ============================================================
        print("[screenshot] 06_timeline - Zoomed timeline area")
        timeline_el = page.locator('[class*="timeline-tracks"], [class*="TimelineTrack"]').first
        try:
            if await timeline_el.is_visible(timeout=3000):
                box = await timeline_el.bounding_box()
                if box:
                    path = OUTPUT_DIR / "06_timeline.png"
                    await page.screenshot(
                        path=str(path),
                        clip={
                            "x": max(0, box["x"]),
                            "y": max(0, box["y"] - 30),
                            "width": min(1920, box["width"]),
                            "height": box["height"] + 60,
                        },
                    )
                    saved_files.append(path)
                    print(f"  Saved: {path}")
        except Exception as e:
            print(f"  Timeline screenshot skipped: {e}")

        await context.close()
        await browser.close()

    print(f"\n[done] {len(saved_files)} screenshots saved to {OUTPUT_DIR}")
    return saved_files


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Take LP screenshots via Playwright")
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    parser.add_argument("--sequence-id", default=DEFAULT_SEQUENCE_ID)
    parser.add_argument("--headless", action="store_true", default=False)
    args = parser.parse_args()

    asyncio.run(take_screenshots(
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
