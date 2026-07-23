"""Free disk space lookups for the logo menu's folder displays. Kept
dependency-free (stdlib only) since settings.py and ytdlp_utils.py
already import from each other in places - this avoids adding a cycle.
"""
import os
import shutil


def format_bytes(num_bytes) -> str:
    try:
        size = float(num_bytes)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


def get_free_space_label(path: str) -> str:
    """Returns a display string like 'C: 123.3 GB free', or '' if the
    path doesn't exist / free space can't be determined. Falls back to
    just the size (no drive letter) on platforms without one."""
    if not path or not os.path.exists(path):
        return ""
    try:
        _, _, free = shutil.disk_usage(path)
    except OSError:
        return ""
    drive, _ = os.path.splitdrive(path)
    free_str = format_bytes(free)
    return f"{drive} {free_str} free" if drive else f"{free_str} free"
