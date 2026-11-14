"""
Camoufox automation controller - Milestone 1
Provides async functions for browser lifecycle management with persistent profiles.
Designed to run INSIDE the KasmVNC container where DISPLAY is available.
"""
import asyncio
import os
import json
import pathlib
from typing import Optional


PROFILE_DIR = os.environ.get("CAMOUFOX_PROFILE_DIR", "/home/kasm-user/camoufox-profile")
TEST_PAGE_URL = "http://127.0.0.1:8888/test_page.html"


async def launch_browser(
    profile_dir: Optional[str] = None,
    headless: bool = False,
) -> tuple:
    """
    Launch Camoufox with a persistent profile via Playwright.
    Returns (context, page) tuple.
    The browser window is visible on the KasmVNC desktop when headless=False.
    """
    from camoufox.async_api import AsyncCamoufox

    profile = profile_dir or PROFILE_DIR
    os.makedirs(profile, exist_ok=True)

    # AsyncCamoufox as context manager returns a BrowserContext (persistent)
    camoufox = AsyncCamoufox(
        headless=headless,
        persistent_context=True,
        user_data_dir=str(profile),
        os="linux",
        # Software-only server: avoid the upstream host-GPU probe.
        # Canvas masking is an explicit privacy trade-off, not a stealth claim.
        canvas_noise=True,
    )
    context = await camoufox.__aenter__()

    # Open a page
    page = await context.new_page()

    return camoufox, context, page


async def close_browser(camoufox_cm) -> None:
    """Gracefully close the browser via context manager exit."""
    await camoufox_cm.__aexit__(None, None, None)


async def navigate_and_click(page, url: str) -> dict:
    """Navigate to a test page, click the button, and return results."""
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_selector("#marker")

    # Read initial marker
    initial = await page.text_content("#marker")

    # Click the button
    await page.click("#clickme")
    await page.wait_for_timeout(500)

    # Read updated marker
    updated = await page.text_content("#marker")

    return {"initial_marker": initial, "clicked_marker": updated}


async def set_persistent_data(page, key: str, value: str) -> None:
    """Set data in localStorage for persistence testing."""
    await page.evaluate(f"localStorage.setItem('{key}', '{value}')")


async def get_persistent_data(page, url: str, key: str) -> Optional[str]:
    """Navigate to URL and read localStorage value."""
    await page.goto(url, wait_until="domcontentloaded")
    result = await page.evaluate(f"localStorage.getItem('{key}')")
    return result


async def get_cookies(context) -> list:
    """Get all cookies from the context."""
    return await context.cookies()


async def get_profile_size(profile_dir: Optional[str] = None) -> dict:
    """Return basic stats about the profile directory."""
    profile = pathlib.Path(profile_dir or PROFILE_DIR)
    if not profile.exists():
        return {"exists": False, "files": 0, "total_bytes": 0}

    files = list(profile.rglob("*"))
    file_count = sum(1 for f in files if f.is_file())
    total_bytes = sum(f.stat().st_size for f in files if f.is_file())

    return {
        "exists": True,
        "files": file_count,
        "total_bytes": total_bytes,
        "path": str(profile),
    }
