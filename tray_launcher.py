"""StashDLP Windows system-tray launcher.

The launcher deliberately does not reimplement the FastAPI/uvicorn startup.
It starts the existing backend/main.py as a separate process and only owns
that process's lifetime and its Windows console/tray UI.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import pystray
from PIL import Image

from backend.config import PORT

PROJECT_ROOT = Path(__file__).resolve().parent
APP_NAME = "Stash DLP Web"
APP_URL = f"http://127.0.0.1:{PORT}"
APP_ENTRY = PROJECT_ROOT / "backend" / "main.py"
LOGO_PATH = PROJECT_ROOT / "static" / "logo.png"

SW_HIDE = 0
SW_SHOW = 5

kernel32 = ctypes.windll.kernel32
user32 = ctypes.windll.user32
kernel32.GetConsoleWindow.restype = ctypes.c_void_p
kernel32.AttachConsole.argtypes = [ctypes.c_uint32]
kernel32.AttachConsole.restype = ctypes.c_bool
kernel32.FreeConsole.argtypes = []
kernel32.FreeConsole.restype = ctypes.c_bool


class TrayLauncher:
    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self.console_hwnd: int | None = None
        self.icon: pystray.Icon | None = None
        self._stopping = False

    def start_server(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return

        # This is the app's existing startup entry point. The server itself
        # still calls uvicorn.run(...); this launcher does not duplicate it.
        # The tray is intentionally started with pythonw.exe, but the app
        # itself must be started with the normal console Python so that its
        # existing console can be shown/hidden from the tray menu.
        python_exe = Path(sys.executable)
        if python_exe.name.lower() == "pythonw.exe":
            python_exe = python_exe.with_name("python.exe")
        command = [str(python_exe), str(APP_ENTRY)]

        creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        self.process = subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            creationflags=creationflags,
        )

        # Give Windows a moment to create the child's console window, then
        # capture that window handle so the tray can hide/show it.
        self.console_hwnd = self._find_console_window(self.process.pid)
        if self.console_hwnd:
            self._show_console(False)

    def _find_console_window(self, pid: int) -> int | None:
        """Attach briefly to the child's console and obtain its HWND."""
        for _ in range(30):
            if kernel32.AttachConsole(pid):
                try:
                    hwnd = kernel32.GetConsoleWindow()
                finally:
                    kernel32.FreeConsole()
                if hwnd:
                    return int(hwnd)
            time.sleep(0.1)
        return None

    def _show_console(self, visible: bool) -> None:
        if self.console_hwnd:
            user32.ShowWindow(self.console_hwnd, SW_SHOW if visible else SW_HIDE)

    def toggle_console(self, _icon=None, _item=None) -> None:
        if not self.console_hwnd:
            self.console_hwnd = self._find_console_window(
                self.process.pid if self.process else 0
            )
        if self.console_hwnd:
            visible = bool(user32.IsWindowVisible(self.console_hwnd))
            self._show_console(not visible)

    def open_app(self, _icon=None, _item=None) -> None:
        webbrowser.open(APP_URL)

    def restart_server(self, _icon=None, _item=None) -> None:
        self.stop_server()
        if not self._stopping:
            self.start_server()

    def stop_server(self) -> None:
        process = self.process
        self.process = None
        self.console_hwnd = None
        if not process or process.poll() is not None:
            return

        # main.py uses reload=False, so there is no uvicorn supervisor tree.
        # Still use taskkill /T as a defensive Windows process-tree cleanup.
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()

    def quit(self, _icon=None, _item=None) -> None:
        self._stopping = True
        self.stop_server()
        if self.icon:
            self.icon.stop()

    def build_icon(self) -> pystray.Icon:
        image = Image.open(LOGO_PATH).convert("RGBA")
        menu = pystray.Menu(
            pystray.MenuItem(f"Open {APP_NAME}", self.open_app, default=True),
            pystray.MenuItem("Show/Hide Console", self.toggle_console),
            pystray.MenuItem("Restart Server", self.restart_server),
            pystray.MenuItem("Quit", self.quit),
        )
        self.icon = pystray.Icon("StashDLP", image, APP_NAME, menu)
        return self.icon

    def run(self) -> None:
        self.start_server()
        icon = self.build_icon()
        icon.run()


if __name__ == "__main__":
    TrayLauncher().run()
