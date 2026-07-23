"""Async wrappers around the yt-dlp CLI. These replace the QProcess-based
TitleFetcher / DownloadWorker.run() / boot_filesystem_scan update-check
logic from the desktop app, using asyncio subprocesses instead of Qt's
event loop.
"""
import asyncio
import os
import re
from urllib.parse import urlparse

from config import AUDIO_EXTENSIONS
from procflags import NO_CONSOLE_KWARGS
from settings import get_save_dir


def is_audio_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in AUDIO_EXTENSIONS


def clean_filename(raw: str) -> str:
    s = re.sub(r'[\\/:*?"<>|]', "-", raw)
    return re.sub(r"\s+", " ", s).strip()


def get_domain(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
        host = re.sub(r"^(www\d?|m)\.", "", host)
        return host.split(".")[0]
    except Exception:
        return ""


def format_file_size(num_bytes) -> str:
    try:
        size = float(num_bytes)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


def find_media_file(filename: str):
    """Returns the full path to the actual downloaded file matching this
    job's filename stem (preferring .mp4), or None if nothing's there."""
    save_dir = get_save_dir()
    mp4_path = os.path.join(save_dir, filename + ".mp4")
    if os.path.isfile(mp4_path):
        return mp4_path
    try:
        for fname in os.listdir(save_dir):
            full_path = os.path.join(save_dir, fname)
            if os.path.isfile(full_path) and os.path.splitext(fname)[0] == filename:
                return full_path
    except OSError:
        pass
    return None


def get_downloaded_file_size(filename: str):
    path = find_media_file(filename)
    if path is None:
        return None
    try:
        return os.path.getsize(path)
    except OSError:
        return None


async def fetch_title(url: str) -> str:
    """Mirrors TitleFetcher.run()."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp",
            "--get-title",
            "--no-warnings",
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **NO_CONSOLE_KWARGS,
        )
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
        stdout = stdout_bytes.decode("utf-8", errors="ignore")
        title = stdout.strip().splitlines()[0] if stdout.strip() else "Unknown Title"
        return title
    except Exception:
        return "Unknown Title"


async def check_and_update_ytdlp():
    """Mirrors boot_filesystem_scan/finalize_boot_scan's version + update
    detection. Returns (version, just_updated)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp",
            "-U",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **NO_CONSOLE_KWARGS,
        )
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout_bytes.decode("utf-8", errors="ignore")
    except Exception:
        output = ""

    updated_match = re.search(r"Updated yt-dlp to version\s+([\d.]+)", output, re.IGNORECASE)
    uptodate_match = re.search(r"up to date\s*\(?\s*([\d.]+)", output, re.IGNORECASE)

    if updated_match:
        return updated_match.group(1), True

    if uptodate_match:
        return uptodate_match.group(1), False

    return await fetch_version_sync(), False


async def fetch_version_sync() -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp",
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **NO_CONSOLE_KWARGS,
        )
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        out = stdout_bytes.decode("utf-8", errors="ignore").strip()
        return out.splitlines()[0].strip() if out else ""
    except Exception:
        return ""
