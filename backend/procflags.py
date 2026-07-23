"""On Windows, spawning a console app (yt-dlp.exe, ffmpeg.exe) via
subprocess normally flashes a console window briefly, even though this
app itself has none. CREATE_NO_WINDOW suppresses that. It's a
Windows-only constant - subprocess.CREATE_NO_WINDOW doesn't exist at all
on Linux/macOS - so this is built conditionally and is a no-op there.

Usage: asyncio.create_subprocess_exec(..., **NO_CONSOLE_KWARGS)
"""
import subprocess
import sys

NO_CONSOLE_KWARGS = {}
if sys.platform == "win32":
    NO_CONSOLE_KWARGS["creationflags"] = subprocess.CREATE_NO_WINDOW
