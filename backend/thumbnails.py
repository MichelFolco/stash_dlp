"""Thumbnail resolution for ledger cards.

All thumbnails live in the current download folder's subfolder under
the central library_data store (config.LIBRARY_DATA_DIR), under
.thumbnails/<filename>.jpg - isolated from the actual media files, and
named plainly (no ".thumb" suffix) since there's no longer a naming
collision to worry about.

Preference order:
  1. Already present in the thumbnails folder - either yt-dlp's own
     thumbnail (written there directly during download) or a
     previously-cached ffmpeg frame grab
  2. A fresh ffmpeg grab, cached there for next time: a video frame a few
     seconds in, or - for audio files - the cover art embedded in the file
  3. None - the frontend shows its own placeholder icon
"""
import asyncio
import os

from procflags import NO_CONSOLE_KWARGS
from settings import get_thumbnails_dir
from ytdlp_utils import find_media_file, is_audio_file

FFMPEG_TIMEOUT = 15

# (media path, mtime) pairs ffmpeg already failed on - typically audio files
# with no embedded art. Without this, every list refresh would spawn ffmpeg
# again for each of them.
_NO_THUMBNAIL: set = set()


def thumbnail_path_for(filename: str) -> str:
    return os.path.join(get_thumbnails_dir(), filename + ".jpg")


async def get_thumbnail_path(filename: str):
    existing = thumbnail_path_for(filename)
    if os.path.exists(existing):
        return existing

    media_path = find_media_file(filename)
    if not media_path:
        return None

    try:
        key = (media_path, os.path.getmtime(media_path))
    except OSError:
        return None
    if key in _NO_THUMBNAIL:
        return None

    ok = await _extract_frame(media_path, existing, audio=is_audio_file(media_path))
    if not ok:
        _NO_THUMBNAIL.add(key)
    return existing if ok else None


async def _extract_frame(media_path: str, out_path: str, audio: bool = False) -> bool:
    if audio:
        # Embedded cover art is a single attached picture stream. Any -ss
        # (even 0) makes ffmpeg emit nothing for it, so audio is read
        # unseeked and the picture stream is mapped explicitly - a file with
        # no art then fails cleanly instead of grabbing something else.
        attempts = [["-i", media_path, "-map", "0:v:0", "-frames:v", "1", "-update", "1"]]
    else:
        attempts = [["-ss", seek, "-i", media_path, "-frames:v", "1"] for seek in ("3", "0")]
    for args in attempts:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-y",
                *args,
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
