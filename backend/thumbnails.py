"""Thumbnail resolution for ledger cards.

All thumbnails live in the current download folder's subfolder under
the central library_data store (config.LIBRARY_DATA_DIR), under
.thumbnails/<filename>.jpg - isolated from the actual media files, and
named plainly (no ".thumb" suffix) since there's no longer a naming
collision to worry about.

Preference order:
  1. Already present in the thumbnails folder - either yt-dlp's own
     thumbnail (relocated there after download by job_manager) or a
     previously-cached ffmpeg frame grab
  2. A fresh ffmpeg frame grab (a few seconds in), cached there for next time
  3. None - the frontend shows its own placeholder icon
"""
import asyncio
import os

from procflags import NO_CONSOLE_KWARGS
from settings import get_thumbnails_dir
from ytdlp_utils import find_media_file

FFMPEG_TIMEOUT = 15


def thumbnail_path_for(filename: str) -> str:
    return os.path.join(get_thumbnails_dir(), filename + ".jpg")


async def get_thumbnail_path(filename: str):
    existing = thumbnail_path_for(filename)
    if os.path.exists(existing):
        return existing

    media_path = find_media_file(filename)
    if not media_path:
        return None

    ok = await _extract_frame(media_path, existing)
    return existing if ok else None


async def _extract_frame(media_path: str, out_path: str) -> bool:
    for seek in ("3", "0"):
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-y",
                "-ss", seek,
                "-i", media_path,
                "-frames:v", "1",
                "-vf", "scale=320:-1",
                out_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                **NO_CONSOLE_KWARGS,
            )
            await asyncio.wait_for(proc.wait(), timeout=FFMPEG_TIMEOUT)
        except Exception:
            continue
        if os.path.exists(out_path):
            return True
    return False
