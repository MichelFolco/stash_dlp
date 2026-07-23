"""Finds a .m3u8 stream URL on a page by watching network requests.
Ported from M3u8InterceptorWorker (playwright sync_api + QThread) to
playwright's async_api, which plays nicer with FastAPI's event loop.
"""
import asyncio
import os

try:
    from playwright.async_api import async_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


class M3u8NotFound(Exception):
    pass


async def find_m3u8(target_url: str):
    """Returns (stream_url, page_title). Raises M3u8NotFound on failure."""
    if not PLAYWRIGHT_AVAILABLE:
        raise M3u8NotFound("playwright module not installed.")

    user_localappdata = os.environ.get("LOCALAPPDATA", "")
    if user_localappdata:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(
            user_localappdata, "ms-playwright"
        )

    detected_url = None
    page_title = ""

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            def handle_request(request):
                nonlocal detected_url
                url = request.url
                if ".m3u8" in url.lower() and not detected_url:
                    detected_url = url

            page.on("request", handle_request)

            try:
                await page.goto(target_url, wait_until="networkidle", timeout=15000)
                page_title = (await page.title()).strip()
            except Exception:
                pass

            await asyncio.sleep(3)
            if not page_title:
                try:
                    await page.goto(target_url, wait_until="networkidle", timeout=15000)
                    page_title = (await page.title()).strip()
                except Exception:
                    page_title = ""

            await browser.close()
    except Exception as e:
        raise M3u8NotFound(str(e))

    if not detected_url:
        raise M3u8NotFound("No m3u8 stream detected on page.")

    return detected_url, page_title
