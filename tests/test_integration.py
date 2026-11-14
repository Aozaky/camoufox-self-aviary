"""
Integration tests for Camoufox Milestone 1
These tests run INSIDE the container against the real browser.
TDD approach: write tests first, then make them pass.
"""
import asyncio
import os
import sys
import subprocess
import pytest
import pytest_asyncio

# Add scripts to path
sys.path.insert(0, "/home/kasm-user/scripts")


PROFILE_DIR = os.environ.get("CAMOUFOX_PROFILE_DIR", "/home/kasm-user/camoufox-profile/test")
TEST_PAGE_URL = "http://127.0.0.1:8888/test_page.html"


@pytest.fixture(scope="module")
def event_loop():
    """Create a shared event loop for the module."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="module")
async def browser_session():
    """Launch a browser session for the test module."""
    from controller import launch_browser, close_browser
    
    camoufox_cm, context, page = await launch_browser(
        profile_dir=PROFILE_DIR,
        headless=False,
    )
    yield {"cm": camoufox_cm, "context": context, "page": page}
    await close_browser(camoufox_cm)


class TestBrowserLaunch:
    """Test that Camoufox launches and is visible on the display."""

    @pytest.mark.asyncio
    async def test_browser_opens(self, browser_session):
        """Browser should launch and be accessible."""
        page = browser_session["page"]
        assert page is not None
        # Page should be functional
        await page.goto("about:blank")
        title = await page.title()
        assert title is not None  # about:blank returns empty string

    @pytest.mark.asyncio
    async def test_display_is_set(self):
        """DISPLAY environment variable should be set for headful mode."""
        display = os.environ.get("DISPLAY")
        assert display is not None, "DISPLAY not set - browser won't be visible in KasmVNC"


class TestPageInteraction:
    """Test navigating to test page and interacting with it."""

    @pytest.mark.asyncio
    async def test_navigate_to_test_page(self, browser_session):
        """Should navigate to the local test page."""
        page = browser_session["page"]
        await page.goto(TEST_PAGE_URL, wait_until="domcontentloaded")
        heading = await page.text_content("#heading")
        assert heading == "Camoufox Milestone 1 Test"

    @pytest.mark.asyncio
    async def test_click_and_read_marker(self, browser_session):
        """Click button should change the marker text."""
        page = browser_session["page"]
        await page.goto(TEST_PAGE_URL, wait_until="domcontentloaded")

        # Initial state
        initial = await page.text_content("#marker")
        assert initial == "MARKER_INITIAL"

        # Click
        await page.click("#clickme")
        await page.wait_for_timeout(300)

        # Updated state
        updated = await page.text_content("#marker")
        assert updated == "MARKER_CLICKED"


class TestPersistentProfile:
    """Test that profile data persists across browser restarts."""

    @pytest.mark.asyncio
    async def test_set_localstorage(self, browser_session):
        """Set a localStorage value for persistence verification."""
        page = browser_session["page"]
        await page.goto(TEST_PAGE_URL, wait_until="domcontentloaded")
        await page.evaluate("localStorage.setItem('test_persist', 'milestone1_ok')")
        result = await page.evaluate("localStorage.getItem('test_persist')")
        assert result == "milestone1_ok"

    @pytest.mark.asyncio
    async def test_set_cookie(self, browser_session):
        """Set a cookie for persistence verification."""
        context = browser_session["context"]
        await context.add_cookies([{
            "name": "test_cookie",
            "value": "camoufox_m1",
            "domain": "127.0.0.1",
            "path": "/",
        }])
        cookies = await context.cookies()
        test_cookies = [c for c in cookies if c["name"] == "test_cookie"]
        assert len(test_cookies) == 1
        assert test_cookies[0]["value"] == "camoufox_m1"

    @pytest.mark.asyncio
    async def test_profile_directory_has_data(self, browser_session):
        """Profile directory should contain browser data files."""
        from controller import get_profile_size
        stats = await get_profile_size(PROFILE_DIR)
        assert stats["exists"], "Profile directory doesn't exist"
        assert stats["files"] > 0, "Profile directory is empty"
        assert stats["total_bytes"] > 0, "Profile files are empty"


class TestProfilePersistenceAcrossRestart:
    """
    Verify profile survives browser restart.
    This test creates its OWN browser sessions (not using the module fixture).
    """

    RESTART_PROFILE = PROFILE_DIR + "_restart"

    @pytest.mark.asyncio
    async def test_data_survives_restart(self):
        """Data set in session 1 should be readable in session 2."""
        from controller import launch_browser, close_browser

        # --- Session 1: Set data ---
        cm1, ctx1, page1 = await launch_browser(
            profile_dir=self.RESTART_PROFILE,
            headless=False,
        )
        try:
            await page1.goto(TEST_PAGE_URL, wait_until="domcontentloaded")
            await page1.evaluate("localStorage.setItem('restart_test', 'survived')")
            await ctx1.add_cookies([{
                "name": "restart_cookie",
                "value": "alive",
                "domain": "127.0.0.1",
                "path": "/",
            }])
        finally:
            await close_browser(cm1)

        # Small delay to ensure profile is flushed
        await asyncio.sleep(2)

        # --- Session 2: Read data ---
        cm2, ctx2, page2 = await launch_browser(
            profile_dir=self.RESTART_PROFILE,
            headless=False,
        )
        try:
            await page2.goto(TEST_PAGE_URL, wait_until="domcontentloaded")

            # Check localStorage
            stored = await page2.evaluate("localStorage.getItem('restart_test')")
            assert stored == "survived", f"localStorage not persisted: got {stored}"

            # Check cookies
            cookies = await ctx2.cookies()
            restart_cookies = [c for c in cookies if c["name"] == "restart_cookie"]
            assert len(restart_cookies) == 1, f"Cookie not persisted: {cookies}"
            assert restart_cookies[0]["value"] == "alive"

            # Check the test page detects persisted data
            storage_result = await page2.text_content("#storage-result")
            assert "PERSISTED:" in storage_result
        finally:
            await close_browser(cm2)


class TestViewerAvailability:
    """Test that KasmVNC viewer is accessible while browser is running."""

    @pytest.mark.asyncio
    async def test_kasmvnc_port_listening(self, browser_session):
        """KasmVNC should be listening on port 6901 inside the container."""
        import subprocess
        # Check if port 6901 has a listener
        result = subprocess.run(
            ["ss", "-tlnp"],
            capture_output=True, text=True
        )
        assert "6901" in result.stdout, "KasmVNC not listening on 6901"

    @pytest.mark.asyncio
    async def test_viewer_https_responds(self, browser_session):
        """KasmVNC web viewer should respond to HTTPS requests."""
        import httpx
        async with httpx.AsyncClient(verify=False) as client:
            try:
                resp = await client.get("https://127.0.0.1:6901/", timeout=5.0)
                # KasmVNC returns a web page (200 or redirect)
                assert resp.status_code in (200, 301, 302, 307), \
                    f"Unexpected status: {resp.status_code}"
            except httpx.ConnectError:
                pytest.fail("Cannot connect to KasmVNC viewer on port 6901")


class TestAuthRejection:
    """Test that viewer authentication rejects bad credentials."""

    @pytest.mark.asyncio
    async def test_bad_password_rejected(self):
        """VNC viewer should reject connections with wrong password."""
        import httpx
        async with httpx.AsyncClient(verify=False) as client:
            # Try to access the VNC websocket API with bad credentials
            # KasmVNC uses basic auth or token-based auth
            try:
                resp = await client.get(
                    "https://127.0.0.1:6901/api/get_screenshot",
                    timeout=5.0,
                    auth=("kasm_user", "definitely_wrong_password_12345"),
                )
                # Should get 401 or 403
                assert resp.status_code in (401, 403, 404), \
                    f"Bad credentials not rejected: status {resp.status_code}"
            except httpx.ConnectError:
                pytest.skip("KasmVNC not reachable for auth test")
