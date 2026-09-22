"""Finds a .m3u8 stream URL on a page by watching network requests.
Ported from M3u8InterceptorWorker (playwright sync_api + QThread) to
playwright's async_api, which plays nicer with FastAPI's event loop.
"""
import asyncio
import os

try:
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


class M3u8NotFound(Exception):
    pass


_sniff_lock = asyncio.Lock()
_MAX_ATTEMPTS = 2
_RETRY_DELAY_SECONDS = 1.5


async def find_m3u8(target_url: str):
    if not PLAYWRIGHT_AVAILABLE:
        raise M3u8NotFound("playwright module not installed.")

    user_localappdata = os.environ.get("LOCALAPPDATA", "")
    if user_localappdata:
        os.environ.setdefault(
            "PLAYWRIGHT_BROWSERS_PATH",
            os.path.join(user_localappdata, "ms-playwright"),
        )

    last_error = None
    async with _sniff_lock:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                return await _sniff_once(target_url)
            except M3u8NotFound:
                raise
            except Exception as e:
                last_error = e
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_RETRY_DELAY_SECONDS)

    raise M3u8NotFound(f"{type(last_error).__name__}: {last_error}")


async def _sniff_once(target_url: str):
    detected_url = None
    page_title = ""

    async with async_playwright() as p:
        browser = None
        try:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            )
            page = await context.new_page()

            def handle_request(request):
                nonlocal detected_url
                url = request.url
                if not detected_url and ".m3u8" in url.lower():
                    detected_url = url

            page.on("request", handle_request)

            # Use "domcontentloaded" - networkidle hangs forever on
            # pages with heartbeat/polling connections (i.e. every
            # streaming site). Then just wait a fixed window for the
            # player to fire its m3u8 request.
            try:
                await page.goto(
                    target_url,
                    wait_until="domcontentloaded",
                    timeout=20000,
                )
            except PWTimeout:
                # Slow page - keep going, we still might see the m3u8.
                pass
            except Exception:
                pass

            # Poll for the title / m3u8 instead of a blind sleep, so we
            # bail out the moment we have what we need.
            for _ in range(30):  # up to ~15s
                if detected_url:
                    break
                try:
                    page_title = (await page.title()).strip()
                except Exception:
                    pass
                await asyncio.sleep(0.5)

            if not page_title:
                try:
                    page_title = (await page.title()).strip()
                except Exception:
                    page_title = ""

        finally:
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass

    if not detected_url:
        raise M3u8NotFound("No m3u8 stream detected on page.")

    return detected_url, page_title
