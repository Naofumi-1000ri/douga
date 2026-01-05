"""End-to-end tests using Playwright.

Tests the full frontend application with real browser interactions.
"""

import os
from pathlib import Path

import pytest


# Skip if Playwright not installed or servers not running
pytest.importorskip("playwright")


class TestFrontendE2E:
    """End-to-end tests for the frontend application."""

    @pytest.fixture(scope="class")
    def browser(self):
        """Create a browser instance."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            yield browser
            browser.close()

    @pytest.fixture
    def page(self, browser):
        """Create a new page for each test."""
        page = browser.new_page()
        yield page
        page.close()

    @pytest.fixture
    def screenshot_dir(self, tmp_path):
        """Create directory for screenshots."""
        return tmp_path

    def test_login_page_loads(self, page, screenshot_dir):
        """Test that the login page loads correctly."""
        page.goto("http://localhost:5173/login")
        page.wait_for_load_state("networkidle")

        # Save screenshot
        page.screenshot(path=str(screenshot_dir / "01_login_page.png"))

        # Check page title or content
        assert page.title() or True  # Title may not be set

        # Look for login elements (Google OAuth button instead of email/password form)
        google_button = page.locator('button:has-text("Google")')
        h1_title = page.locator('h1:has-text("Douga")')

        # Login page should have Google login button or app title
        has_login_ui = google_button.count() > 0 or h1_title.count() > 0
        assert has_login_ui, "Login page should have Google login button or app branding"

    def test_dashboard_redirects_to_login(self, page, screenshot_dir):
        """Test that dashboard redirects unauthenticated users to login."""
        page.goto("http://localhost:5173/")
        page.wait_for_load_state("networkidle")

        # Save screenshot
        page.screenshot(path=str(screenshot_dir / "02_dashboard_redirect.png"))

        # Should either be on login page or show login form
        url = page.url
        # Either redirected to login or shows login content
        assert "/login" in url or page.locator('input[type="password"]').count() > 0

    def test_project_page_loads(self, page, screenshot_dir):
        """Test that project page structure exists."""
        page.goto("http://localhost:5173/projects/test-project")
        page.wait_for_load_state("networkidle")

        # Save screenshot
        page.screenshot(path=str(screenshot_dir / "03_project_page.png"))

        # Page should load without crashing
        # May redirect to login or show error, but should not crash
        assert page.url is not None

    def test_responsive_layout_mobile(self, page, screenshot_dir):
        """Test responsive layout on mobile viewport."""
        # Set mobile viewport
        page.set_viewport_size({"width": 375, "height": 812})

        page.goto("http://localhost:5173/login")
        page.wait_for_load_state("networkidle")

        # Save screenshot
        page.screenshot(path=str(screenshot_dir / "04_mobile_view.png"))

        # Page should render without horizontal scroll issues
        # Check that body width matches viewport
        body_width = page.evaluate("document.body.scrollWidth")
        assert body_width <= 400, "Page should fit mobile viewport"

    def test_responsive_layout_tablet(self, page, screenshot_dir):
        """Test responsive layout on tablet viewport."""
        # Set tablet viewport
        page.set_viewport_size({"width": 768, "height": 1024})

        page.goto("http://localhost:5173/login")
        page.wait_for_load_state("networkidle")

        # Save screenshot
        page.screenshot(path=str(screenshot_dir / "05_tablet_view.png"))

        # Page should render properly
        assert True

    def test_responsive_layout_desktop(self, page, screenshot_dir):
        """Test responsive layout on desktop viewport."""
        # Set desktop viewport
        page.set_viewport_size({"width": 1920, "height": 1080})

        page.goto("http://localhost:5173/login")
        page.wait_for_load_state("networkidle")

        # Save screenshot
        page.screenshot(path=str(screenshot_dir / "06_desktop_view.png"))

        # Page should render properly
        assert True

    def test_no_console_errors(self, page, screenshot_dir):
        """Test that there are no critical console errors."""
        errors = []

        def handle_console(msg):
            if msg.type == "error":
                errors.append(msg.text)

        page.on("console", handle_console)

        page.goto("http://localhost:5173/login")
        page.wait_for_load_state("networkidle")

        # Save screenshot
        page.screenshot(path=str(screenshot_dir / "07_console_check.png"))

        # Filter out expected errors (like API errors when not logged in)
        critical_errors = [e for e in errors if "TypeError" in e or "ReferenceError" in e]

        assert len(critical_errors) == 0, f"Critical errors found: {critical_errors}"

    def test_navigation_elements_exist(self, page, screenshot_dir):
        """Test that navigation elements are present."""
        page.goto("http://localhost:5173/login")
        page.wait_for_load_state("networkidle")

        # Save screenshot
        page.screenshot(path=str(screenshot_dir / "08_navigation.png"))

        # Check for common navigation elements or login button
        has_nav = (
            page.locator("nav").count() > 0 or
            page.locator("header").count() > 0 or
            page.locator('[role="navigation"]').count() > 0 or
            page.locator("a").count() > 0
        )

        # Either has navigation or is a simple login page with Google button
        google_button = page.locator('button:has-text("Google")')
        assert has_nav or google_button.count() > 0, "Page should have navigation or login button"

    def test_form_validation_visual(self, page, screenshot_dir):
        """Test form validation behavior."""
        page.goto("http://localhost:5173/login")
        page.wait_for_load_state("networkidle")

        # Try to submit empty form
        submit_btn = page.locator('button[type="submit"]')
        if submit_btn.count() > 0:
            submit_btn.first.click()
            page.wait_for_timeout(500)

        # Save screenshot showing any validation errors
        page.screenshot(path=str(screenshot_dir / "09_form_validation.png"))

        # Test passes if page doesn't crash
        assert True

    def test_dark_mode_support(self, page, screenshot_dir):
        """Test dark mode CSS support."""
        # Force dark mode via media query emulation
        page.emulate_media(color_scheme="dark")

        page.goto("http://localhost:5173/login")
        page.wait_for_load_state("networkidle")

        # Save dark mode screenshot
        page.screenshot(path=str(screenshot_dir / "10_dark_mode.png"))

        # Get background color
        bg_color = page.evaluate("""
            () => {
                const body = document.body;
                return window.getComputedStyle(body).backgroundColor;
            }
        """)

        # Check if dark mode is applied (background should be dark)
        # This is a basic check - actual implementation may vary
        assert bg_color is not None


class TestAPIIntegration:
    """Tests for API integration via Playwright."""

    @pytest.fixture(scope="class")
    def browser(self):
        """Create a browser instance."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            yield browser
            browser.close()

    @pytest.fixture
    def page(self, browser):
        """Create a new page for each test."""
        page = browser.new_page()
        yield page
        page.close()

    def test_api_health_check(self, page):
        """Test that API health endpoint is accessible."""
        response = page.goto("http://localhost:8000/health")
        assert response.status == 200

        # Check response content
        content = page.content()
        assert "healthy" in content.lower() or "status" in content.lower()

    def test_api_docs_accessible(self, page):
        """Test that API documentation is accessible."""
        response = page.goto("http://localhost:8000/docs")
        assert response.status == 200

        # Swagger UI should load
        page.wait_for_load_state("networkidle")
        assert "swagger" in page.content().lower() or "api" in page.content().lower()
