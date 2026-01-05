"""
test_user - Playwright E2E Test Agent for Douga App

This test agent performs comprehensive E2E testing of the Douga video editing application.
It simulates a real user workflow: login, create project, upload assets, edit timeline, export.

Usage:
    python -m pytest tests/e2e/test_user.py -v --tb=short
    python tests/e2e/test_user.py  # Run directly
"""

import os
import sys
import json
import uuid
import time
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

try:
    from playwright.sync_api import sync_playwright, Page, Browser, expect
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    print("Warning: Playwright not installed. Run: pip install playwright && python -m playwright install chromium")


@dataclass
class TestResult:
    """Result of a single test case"""
    name: str
    passed: bool
    duration_ms: float
    error: Optional[str] = None
    screenshot: Optional[str] = None


@dataclass
class TestReport:
    """Complete test report"""
    total: int = 0
    passed: int = 0
    failed: int = 0
    results: List[TestResult] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    def add_result(self, result: TestResult):
        self.results.append(result)
        self.total += 1
        if result.passed:
            self.passed += 1
        else:
            self.failed += 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "success_rate": f"{(self.passed/self.total*100):.1f}%" if self.total > 0 else "0%",
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "results": [
                {
                    "name": r.name,
                    "passed": r.passed,
                    "duration_ms": r.duration_ms,
                    "error": r.error,
                    "screenshot": r.screenshot
                }
                for r in self.results
            ]
        }


class TestUser:
    """
    Test User Agent - Simulates real user interactions with the Douga app
    """

    BASE_URL = "http://localhost:5173"
    API_URL = "http://localhost:8000"
    SCREENSHOT_DIR = Path(__file__).parent.parent.parent / "screenshots" / "test_user"
    TEST_FILES_DIR = Path(__file__).parent.parent.parent / "test_files"

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.report = TestReport()
        self.project_id: Optional[str] = None

        # Ensure directories exist
        self.SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        self.TEST_FILES_DIR.mkdir(parents=True, exist_ok=True)

    def setup(self):
        """Initialize browser and page"""
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Playwright is not installed")

        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=self.headless)
        self.page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        self.report.started_at = datetime.now().isoformat()

    def teardown(self):
        """Clean up browser resources"""
        if self.browser:
            self.browser.close()
        if hasattr(self, 'playwright'):
            self.playwright.stop()
        self.report.finished_at = datetime.now().isoformat()

    def screenshot(self, name: str) -> str:
        """Take a screenshot and return the path"""
        timestamp = datetime.now().strftime("%H%M%S")
        filename = f"{timestamp}_{name}.png"
        filepath = self.SCREENSHOT_DIR / filename
        self.page.screenshot(path=str(filepath))
        return str(filepath)

    def run_test(self, name: str, test_func):
        """Run a test and record the result"""
        start_time = time.time()
        screenshot_path = None
        error = None
        passed = False

        try:
            test_func()
            passed = True
            screenshot_path = self.screenshot(f"{name}_success")
        except Exception as e:
            error = str(e)
            screenshot_path = self.screenshot(f"{name}_failed")
            print(f"  ❌ {name}: {error}")

        duration_ms = (time.time() - start_time) * 1000

        result = TestResult(
            name=name,
            passed=passed,
            duration_ms=duration_ms,
            error=error,
            screenshot=screenshot_path
        )
        self.report.add_result(result)

        status = "✅" if passed else "❌"
        print(f"  {status} {name} ({duration_ms:.0f}ms)")

        return passed

    # =========================================================================
    # Test Cases
    # =========================================================================

    def test_health_check(self):
        """Test that both frontend and backend are running"""
        # Check backend health
        response = self.page.request.get(f"{self.API_URL}/health")
        assert response.status == 200, f"Backend health check failed: {response.status}"

        # Check frontend loads
        self.page.goto(self.BASE_URL, wait_until="domcontentloaded")
        self.page.wait_for_timeout(3000)
        assert self.page.url.startswith(self.BASE_URL), "Frontend failed to load"

    def test_dashboard_loads(self):
        """Test that dashboard page loads correctly"""
        self.page.goto(self.BASE_URL, wait_until="domcontentloaded")
        self.page.wait_for_timeout(3000)

        # Should see dashboard elements (project list, create button)
        # In DEV_MODE, user should be auto-logged in
        content = self.page.content()
        assert "プロジェクト" in content or "Dashboard" in content or "新規作成" in content, \
            "Dashboard content not found"

    def test_create_project(self):
        """Test creating a new project"""
        self.page.goto(self.BASE_URL, wait_until="domcontentloaded")
        self.page.wait_for_timeout(3000)

        # Click create project button
        create_btn = self.page.locator('button:has-text("新規作成"), button:has-text("Create")')
        if create_btn.count() > 0:
            create_btn.first.click()
            self.page.wait_for_timeout(1000)

            # Fill in project name if modal appears
            name_input = self.page.locator('input[placeholder*="プロジェクト"], input[name="name"]')
            if name_input.count() > 0:
                name_input.fill(f"Test Project {uuid.uuid4().hex[:8]}")

                # Click create/confirm button
                confirm_btn = self.page.locator('button:has-text("作成"), button:has-text("Create"), button[type="submit"]')
                if confirm_btn.count() > 0:
                    confirm_btn.first.click()
                    self.page.wait_for_timeout(3000)

        # Should navigate to editor or stay on dashboard with new project
        self.page.wait_for_url(lambda url: "/project/" in url or url == f"{self.BASE_URL}/", timeout=5000)

        # Extract project ID if we're on editor page
        if "/project/" in self.page.url:
            self.project_id = self.page.url.split("/project/")[-1].split("?")[0]

    def _get_or_create_project_id(self):
        """Helper to get or create a project ID"""
        if self.project_id:
            return self.project_id

        # Use existing project or create one via API
        response = self.page.request.get(
            f"{self.API_URL}/api/projects",
            headers={"Authorization": "Bearer dev-token"}
        )
        if response.status != 200:
            raise Exception(f"API returned status {response.status}: {response.text()}")

        projects = response.json()
        if projects:
            self.project_id = projects[0]["id"]
        else:
            # Create via API
            response = self.page.request.post(
                f"{self.API_URL}/api/projects",
                headers={"Authorization": "Bearer dev-token", "Content-Type": "application/json"},
                data=json.dumps({"name": "Test Project"})
            )
            if response.status != 200 and response.status != 201:
                raise Exception(f"Failed to create project: {response.status} - {response.text()}")
            self.project_id = response.json()["id"]

        return self.project_id

    def test_editor_loads(self):
        """Test that editor page loads with all components"""
        project_id = self._get_or_create_project_id()

        self.page.goto(f"{self.BASE_URL}/project/{project_id}", wait_until="domcontentloaded")
        self.page.wait_for_timeout(3000)

        # Check for essential editor components
        content = self.page.content()

        # Asset panel
        assert "アセット" in content or "Asset" in content, "Asset panel not found"

        # Timeline tracks
        assert "Narration" in content, "Narration track not found"
        assert "BGM" in content, "BGM track not found"

        # Export button
        assert "エクスポート" in content or "Export" in content, "Export button not found"

    def test_file_upload(self):
        """Test uploading audio files"""
        project_id = self._get_or_create_project_id()

        self.page.goto(f"{self.BASE_URL}/project/{project_id}", wait_until="domcontentloaded")
        self.page.wait_for_timeout(3000)

        # Create test file if not exists
        test_file = self.TEST_FILES_DIR / "test_audio.mp3"
        if not test_file.exists():
            import subprocess
            subprocess.run([
                "ffmpeg", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                "-ac", "2", "-ar", "44100", str(test_file), "-y"
            ], capture_output=True)

        # Upload file
        file_input = self.page.locator('input[type="file"]').first
        if file_input.count() > 0:
            file_input.set_input_files(str(test_file))
            self.page.wait_for_timeout(5000)

            # Verify file appears in asset list
            content = self.page.content()
            assert "test_audio" in content, "Uploaded file not visible in asset list"

    def test_timeline_has_clips(self):
        """Test that clips can be added to timeline"""
        project_id = self._get_or_create_project_id()

        # Add clip via API (since drag-drop is complex in headless)
        response = self.page.request.get(
            f"{self.API_URL}/api/projects/{project_id}",
            headers={"Authorization": "Bearer dev-token"}
        )
        if response.status != 200:
            raise Exception(f"Failed to get project: {response.status}")

        project = response.json()
        timeline = project.get("timeline_data", {})

        # Check if there are any clips
        audio_tracks = timeline.get("audio_tracks", [])
        has_clips = any(len(track.get("clips", [])) > 0 for track in audio_tracks)

        if not has_clips:
            # Get assets
            assets_response = self.page.request.get(
                f"{self.API_URL}/api/projects/{project_id}/assets",
                headers={"Authorization": "Bearer dev-token"}
            )
            if assets_response.status == 200:
                assets = assets_response.json()

                if assets:
                    # Add clip to narration track
                    narration_track = next((t for t in audio_tracks if t.get("type") == "narration"), None)
                    if narration_track:
                        narration_track["clips"] = [{
                            "id": str(uuid.uuid4()),
                            "asset_id": assets[0]["id"],
                            "start_ms": 0,
                            "duration_ms": 5000,
                            "volume": 1.0,
                            "fade_in_ms": 0,
                            "fade_out_ms": 0
                        }]

                        # Update timeline
                        self.page.request.put(
                            f"{self.API_URL}/api/projects/{project_id}/timeline",
                            headers={"Authorization": "Bearer dev-token", "Content-Type": "application/json"},
                            data=json.dumps(timeline)
                        )

        # Reload and verify clips are visible
        self.page.goto(f"{self.BASE_URL}/project/{project_id}", wait_until="domcontentloaded")
        self.page.wait_for_timeout(3000)

        # Look for clip elements in timeline
        clips = self.page.locator('[style*="background-color"]').all()
        timeline_clips = [c for c in clips if c.bounding_box() and c.bounding_box()["x"] > 350]
        assert len(timeline_clips) > 0, "No clips visible in timeline"

    def test_clip_selection(self):
        """Test that clicking a clip shows properties panel"""
        project_id = self._get_or_create_project_id()

        self.page.goto(f"{self.BASE_URL}/project/{project_id}", wait_until="domcontentloaded")
        self.page.wait_for_timeout(3000)

        # Find and click a clip
        clips = self.page.locator('.rounded.cursor-pointer, [style*="background-color"]').all()
        for clip in clips:
            box = clip.bounding_box()
            if box and box["x"] > 400 and box["width"] > 50:  # Likely a timeline clip
                clip.click()
                self.page.wait_for_timeout(1000)
                break

        # Check for properties panel content
        content = self.page.content()
        # Properties panel should show fade controls or clip info
        has_properties = any(term in content for term in ["フェード", "Fade", "削除", "Delete", "ボリューム", "Volume"])
        assert has_properties, "Clip properties panel not shown"

    def test_export_button(self):
        """Test that export button is clickable"""
        project_id = self._get_or_create_project_id()

        self.page.goto(f"{self.BASE_URL}/project/{project_id}", wait_until="domcontentloaded")
        self.page.wait_for_timeout(3000)

        # Find and click export button
        export_btn = self.page.locator('button:has-text("エクスポート"), button:has-text("Export")')
        assert export_btn.count() > 0, "Export button not found"

        export_btn.first.click()
        self.page.wait_for_timeout(2000)

        # Should either start export or show export options
        # For now, just verify the button was clickable

    def test_volume_controls(self):
        """Test that track volume controls work"""
        project_id = self._get_or_create_project_id()

        self.page.goto(f"{self.BASE_URL}/project/{project_id}", wait_until="domcontentloaded")
        self.page.wait_for_timeout(3000)

        # Find volume sliders (range inputs)
        sliders = self.page.locator('input[type="range"]')
        assert sliders.count() > 0, "No volume sliders found"

        # Try to interact with first slider
        first_slider = sliders.first
        box = first_slider.bounding_box()
        if box:
            # Click at different position to change value
            self.page.mouse.click(box["x"] + box["width"] * 0.3, box["y"] + box["height"] / 2)
            self.page.wait_for_timeout(500)

    def test_navigation(self):
        """Test navigation between dashboard and editor"""
        # Go to dashboard
        self.page.goto(self.BASE_URL, wait_until="domcontentloaded")
        self.page.wait_for_timeout(3000)

        initial_url = self.page.url

        # If there are projects, click one to go to editor
        project_links = self.page.locator('a[href*="/project/"]')
        if project_links.count() > 0:
            project_links.first.click()
            self.page.wait_for_timeout(2000)
            assert "/project/" in self.page.url, "Failed to navigate to project"

            # Go back to dashboard
            back_btn = self.page.locator('a[href="/"], button:has-text("戻る"), [aria-label="back"]')
            if back_btn.count() > 0:
                back_btn.first.click()
                self.page.wait_for_timeout(2000)

    # =========================================================================
    # Main Test Runner
    # =========================================================================

    def run_all_tests(self) -> TestReport:
        """Run all tests and return the report"""
        print("\n" + "="*60)
        print("🧪 Test User Agent - Starting E2E Tests")
        print("="*60 + "\n")

        tests = [
            ("Health Check", self.test_health_check),
            ("Dashboard Loads", self.test_dashboard_loads),
            ("Create Project", self.test_create_project),
            ("Editor Loads", self.test_editor_loads),
            ("File Upload", self.test_file_upload),
            ("Timeline Has Clips", self.test_timeline_has_clips),
            ("Clip Selection", self.test_clip_selection),
            ("Export Button", self.test_export_button),
            ("Volume Controls", self.test_volume_controls),
            ("Navigation", self.test_navigation),
        ]

        try:
            self.setup()

            for name, test_func in tests:
                self.run_test(name, test_func)

        finally:
            self.teardown()

        # Print summary
        print("\n" + "="*60)
        print(f"📊 Test Results: {self.report.passed}/{self.report.total} passed")
        print(f"   Success Rate: {(self.report.passed/self.report.total*100):.1f}%" if self.report.total > 0 else "   No tests run")
        print("="*60 + "\n")

        # Save report
        report_path = self.SCREENSHOT_DIR / "test_report.json"
        with open(report_path, "w") as f:
            json.dump(self.report.to_dict(), f, indent=2, ensure_ascii=False)
        print(f"📝 Report saved to: {report_path}")

        return self.report


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description="Douga E2E Test Agent")
    parser.add_argument("--headed", action="store_true", help="Run with browser visible")
    parser.add_argument("--test", type=str, help="Run specific test by name")
    args = parser.parse_args()

    agent = TestUser(headless=not args.headed)

    if args.test:
        # Run specific test
        agent.setup()
        try:
            test_method = getattr(agent, f"test_{args.test}", None)
            if test_method:
                agent.run_test(args.test, test_method)
            else:
                print(f"Test '{args.test}' not found")
        finally:
            agent.teardown()
    else:
        # Run all tests
        report = agent.run_all_tests()

        # Exit with error code if tests failed
        sys.exit(0 if report.failed == 0 else 1)


if __name__ == "__main__":
    main()
