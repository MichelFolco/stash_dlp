"""Windows clipboard monitor for URLs copied while a browser is active."""

import asyncio
import ctypes
import os
import re
import urllib.parse

from settings import get_download_prefs


_URL_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)
_BROWSER_EXES = {
    "firefox.exe", "waterfox.exe", "chrome.exe", "msedge.exe",
    "brave.exe", "opera.exe", "vivaldi.exe", "librewolf.exe",
}

_user32 = ctypes.windll.user32 if os.name == "nt" else None
_kernel32 = ctypes.windll.kernel32 if os.name == "nt" else None

# ctypes defaults to 32-bit integers for many Win32 calls.  That is fatal for
# clipboard handles/pointers on 64-bit Windows because GlobalLock() returns a
# pointer.  Declare the handful of APIs we use explicitly so clipboard reads
# work reliably on normal 64-bit Windows installs.
if _user32 and _kernel32:
    _user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    _user32.OpenClipboard.restype = ctypes.c_bool
    _user32.CloseClipboard.argtypes = []
    _user32.CloseClipboard.restype = ctypes.c_bool
    _user32.GetClipboardData.argtypes = [ctypes.c_uint]
    _user32.GetClipboardData.restype = ctypes.c_void_p
    _user32.GetForegroundWindow.argtypes = []
    _user32.GetForegroundWindow.restype = ctypes.c_void_p
    _user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    _user32.GetWindowTextW.restype = ctypes.c_int
    _user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    _user32.GetWindowThreadProcessId.restype = ctypes.c_ulong

    _kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    _kernel32.GlobalLock.restype = ctypes.c_void_p
    _kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    _kernel32.GlobalUnlock.restype = ctypes.c_bool
    _kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
    _kernel32.OpenProcess.restype = ctypes.c_void_p
    _kernel32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
    _kernel32.QueryFullProcessImageNameW.restype = ctypes.c_bool
    _kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    _kernel32.CloseHandle.restype = ctypes.c_bool


def _foreground_window_info():
    if not _user32 or not _kernel32:
        return "", ""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return "", ""

    title_buf = ctypes.create_unicode_buffer(512)
    _user32.GetWindowTextW(hwnd, title_buf, len(title_buf))

    pid = ctypes.c_ulong()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return title_buf.value, ""

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return title_buf.value, ""
    try:
        path_buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(len(path_buf))
        if _kernel32.QueryFullProcessImageNameW(handle, 0, path_buf, ctypes.byref(size)):
            return title_buf.value, os.path.basename(path_buf.value).lower()
    finally:
        _kernel32.CloseHandle(handle)
    return title_buf.value, ""


def _read_clipboard():
    if not _user32:
        return ""
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    # Other applications can briefly own the clipboard after a copy.
    # Give Windows a few short chances before treating it as unavailable.
    opened = False
    for _ in range(5):
        if _user32.OpenClipboard(None):
            opened = True
            break
        import time
        time.sleep(0.02)
    if not opened:
        return ""
    try:
        handle = _user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = _kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            return ctypes.wstring_at(ptr)
        finally:
            _kernel32.GlobalUnlock(handle)
    finally:
        _user32.CloseClipboard()


def _is_valid_url(value: str) -> bool:
    value = value.strip()
    if len(value) > 8192 or not _URL_RE.match(value):
        return False
    try:
        parsed = urllib.parse.urlparse(value)
        return bool(parsed.scheme in ("http", "https") and parsed.netloc)
    except Exception:
        return False


async def monitor_clipboard(connections):
    """Broadcast newly copied URLs from the Windows clipboard.

    Clipboard monitoring is independent of which application currently has
    focus.  The only exception is StashDLP itself, so copying a URL from one
    of the app's own "Copy Link" actions does not immediately trigger a
    second download.
    """
    if os.name != "nt":
        return

    last_clipboard = None
    while True:
        try:
            prefs = get_download_prefs()
            if not prefs.get("clipboard_monitor", False):
                # Keep tracking the clipboard while disabled so enabling the
                # setting does not immediately replay an old URL.
                text = _read_clipboard().strip()
                last_clipboard = text
                await asyncio.sleep(0.5)
                continue

            title, _exe = _foreground_window_info()
            text = _read_clipboard().strip()
            if text != last_clipboard:
                last_clipboard = text
                if ("stash dlp" not in title.lower() and
                        _is_valid_url(text)):
                    await connections.broadcast({"type": "clipboard_url", "url": text})
        except Exception:
            pass
        await asyncio.sleep(0.5)
