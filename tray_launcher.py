"""System tray launcher for Stash DLP Web (Windows).

Runs the FastAPI/uvicorn server in a background thread and shows a tray
icon (same pattern as YTMusicWeb's pystray usage) with:
  - double-click / "Open Stash DLP" -> opens the ledger in your browser
  - "Quit" -> stops the server and exits

Run this instead of backend/main.py directly when you want it to live in
the tray rather than a console window.
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# ── pythonw.exe fix ────────────────────────────────────────────
# Under pythonw.exe (no console attached), sys.stdout/sys.stderr are None
# rather than just empty. The instant anything tries to write a line to
# them - and uvicorn's logging does this immediately on startup - it
# raises, the background thread dies silently, and since there's no
# console to show the traceback it just looks like nothing happened.
# Redirect both to a log file before anything else gets a chance to log.
if sys.stdout is None or sys.stderr is None:
    _log_path = os.path.join(PROJECT_ROOT, "tray_launcher.log")
    _log_file = open(_log_path, "a", buffering=1, encoding="utf-8")
    sys.stdout = _log_file
    sys.stderr = _log_file

import threading
import webbrowser

BACKEND_DIR = os.path.join(PROJECT_ROOT, "backend")
sys.path.insert(0, BACKEND_DIR)

from config import HOST, PORT

URL = f"http://127.0.0.1:{PORT}"  # always open the browser via loopback locally

import pystray
import uvicorn
from PIL import Image, ImageDraw, ImageFont


def load_icon_image():
    """Looks for a real icon first (drop your own stash_dlp.ico/png in the
    project root, or reuse static/logo.png), falling back to a generated
    placeholder so this works out of the box."""
    candidates = [
        os.path.join(PROJECT_ROOT, "icon.ico"),
        os.path.join(PROJECT_ROOT, "icon.png"),
        os.path.join(PROJECT_ROOT, "static", "logo.png"),
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return Image.open(path).convert("RGBA")
            except Exception:
                pass

    # Fallback: simple generated placeholder (dark tile, cyan "S")
    size = 64
    img = Image.new("RGBA", (size, size), (18, 18, 20, 255))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([2, 2, size - 2, size - 2], radius=10, outline=(0, 255, 255, 255), width=3)
    try:
        font = ImageFont.truetype("arialbd.ttf", 34)
    except Exception:
        font = ImageFont.load_default()
    text = "S"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]), text, fill=(0, 255, 255, 255), font=font)
    return img


class ServerThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        from main import app  # noqa: imported lazily so sys.path is set up first
        config = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning")
        self.server = uvicorn.Server(config)

    def run(self):
        self.server.run()

    def stop(self):
        self.server.should_exit = True


def open_browser(icon=None, item=None):
    webbrowser.open(URL)


def quit_app(icon, item):
    icon.stop()
    server_thread.stop()


if __name__ == "__main__":
    try:
        server_thread = ServerThread()
        server_thread.start()

        tray_icon = pystray.Icon(
            "stash_dlp",
            icon=load_icon_image(),
            title="Stash DLP Manager",
            menu=pystray.Menu(
                pystray.MenuItem("Open Stash DLP", open_browser, default=True),
                pystray.MenuItem("Quit", quit_app),
            ),
        )
        tray_icon.run()
    except Exception:
        import traceback
        traceback.print_exc()  # goes to tray_launcher.log under pythonw.exe
        raise
