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


# Only one Playwright/Chromium session runs at a time. Launching the
# Node driver concurrently is a common trigger for "Connection closed
# while reading from the driver" on Windows - a second launch can race
# a previous session's browser/driver process that's still shutting
# down, and the new driver process fails to come up cleanly.
_sniff_lock = asyncio.Lock()

# That failure mode is usually a one-off hiccup rather than something
# permanently wrong, so it's worth one automatic retry (after letting
# things settle for a moment) before surfacing an error to the user.
_MAX_ATTEMPTS = 2
_RETRY_DELAY_SECONDS = 1.5


async def find_m3u8(target_url: str):
    """Returns (stream_url, page_title). Raises M3u8NotFound on failure."""
    if not PLAYWRIGHT_AVAILABLE:
        raise M3u8NotFound("playwright module not installed.")

    user_localappdata = os.environ.get("LOCALAPPDATA", "")
    if user_localappdata:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(
            user_localappdata, "ms-playwright"
        )

    last_error = None
    async with _sniff_lock:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                return await _sniff_once(target_url)
            except M3u8NotFound:
                # The page genuinely has no stream on it - retrying
                # won't change that, so don't waste time on it.
                raise
            except Exception as e:
                last_error = e
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_RETRY_DELAY_SECONDS)

    raise M3u8NotFound(str(last_error))


async def _sniff_once(target_url: str):
    detected_url = None
    page_title = ""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
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
        finally:
            # Always close the browser, even if something above threw,
            # so a bad page can't leak a chromium process that lingers
            # and makes the *next* launch flaky too.
            try:
                await browser.close()
            except Exception:
                pass

    if not detected_url:
        raise M3u8NotFound("No m3u8 stream detected on page.")

    return detected_url, page_title
