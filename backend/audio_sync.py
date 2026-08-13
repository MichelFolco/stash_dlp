"""Backend for the "Synchronize Audio" feature: lets the user preview a
video with its audio track shifted by a delay (ms), iterate on that delay
with fast re-renders, and confirm the result as the file's permanent
audio-sync state.

Shares the same Converted/<stem>.ext slot that the Encode Manager uses for
re-encode twins (one twin per stem - see find_converted_file), so a file
can have a re-encode twin OR a sync twin, never both. Callers (main.py)
are responsible for checking with the Encode Manager before calling in
here; this module only concerns itself with the sync render/confirm/cancel
lifecycle once that gate has already passed.

Each render is a real ffmpeg encode (not a fast remux) - libx264 + aac,
sane fixed quality settings. Re-opening a previously-synchronized file and
re-tweaking backs up the confirmed twin before the first render of that
session, so a Cancel without re-confirming restores exactly what was
there before; if the file was never synchronized, Cancel just deletes
whatever unconfirmed render was made.
"""
import asyncio
import os

from ffmpeg_encode import probe_basic_info
from procflags import NO_CONSOLE_KWARGS
from settings import get_converted_dir
from ytdlp_utils import clean_filename, find_media_file, format_file_size

RENDER_TIMEOUT = 1800  # 30 min ceiling for a single sync render

# Per-filename asyncio locks, so a double-click or a second browser tab
# can't kick off two ffmpeg renders for the same file at once.
_locks: dict = {}

# filename -> backup path for the confirmed twin that existed before this
# editing session's first re-render. Only populated when re-opening a
# file that was already synchronized; cleared on Confirm or Cancel.
_session_backups: dict = {}


def _lock_for(filename: str) -> asyncio.Lock:
    lock = _locks.get(filename)
    if lock is None:
        lock = asyncio.Lock()
        _locks[filename] = lock
    return lock


def _output_path(filename: str) -> str:
    return os.path.join(get_converted_dir(), clean_filename(filename) + ".mp4")


def _backup_path(filename: str) -> str:
    return os.path.join(get_converted_dir(), clean_filename(filename) + ".sync-backup.mp4")


async def apply_delay(job_manager, filename: str, delay_ms: float) -> dict:
    """Renders (or re-renders) the synced preview file and returns info
    about it for the frontend to reload into the player. Raises
    ValueError for bad input/state, RuntimeError if ffmpeg fails."""
    job = job_manager.jobs.get(filename)
    if not job or job.get("status") != "DONE" or job.get("is_audio"):
        raise ValueError(f"'{filename}' isn't a completed video.")

    media_path = find_media_file(filename)
    if not media_path:
        raise ValueError(f"Couldn't find the file for '{filename}'.")

    async with _lock_for(filename):
        out_path = _output_path(filename)

        # First re-render of a session that re-opened an already-
        # synchronized file: stash the confirmed version aside so
        # Cancel can restore it if the user doesn't re-confirm.
        if job.get("synchronized") and filename not in _session_backups and os.path.exists(out_path):
            backup_path = _backup_path(filename)
            try:
                if os.path.exists(backup_path):
                    os.remove(backup_path)
                os.replace(out_path, backup_path)
                _session_backups[filename] = backup_path
            except OSError as e:
                raise RuntimeError(f"Couldn't stage the previous synced version: {e}")

        tmp_path = out_path + ".tmp"
        delay_s = delay_ms / 1000.0

        cmd = [
            "ffmpeg", "-y",
            "-i", media_path,
            "-itsoffset", f"{delay_s:.3f}",
            "-i", media_path,
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "libx264", "-crf", "20", "-preset", "medium",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            "-shortest",
            "-f", "mp4",
            tmp_path,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                **NO_CONSOLE_KWARGS,
            )
            _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=RENDER_TIMEOUT)
            returncode = proc.returncode
        except FileNotFoundError:
            raise RuntimeError("ffmpeg isn't installed/available on PATH.")
        except asyncio.TimeoutError:
            proc.kill()
            _remove_quiet(tmp_path)
            raise RuntimeError("The sync render timed out.")

        if returncode != 0 or not os.path.exists(tmp_path):
            _remove_quiet(tmp_path)
            tail = (stderr_bytes or b"").decode("utf-8", errors="ignore").strip().splitlines()
            detail = " | ".join(tail[-3:]) if tail else "Unknown ffmpeg error."
            raise RuntimeError(f"ffmpeg failed to render the synced audio: {detail}")

        try:
            os.replace(tmp_path, out_path)
        except OSError as e:
            _remove_quiet(tmp_path)
            raise RuntimeError(f"Couldn't finalize the synced render: {e}")

        info = await probe_basic_info(out_path)
        size_bytes = os.path.getsize(out_path)

        return {
            "filename": filename,
            "output_filename": os.path.basename(out_path),
            "size_label": format_file_size(size_bytes),
            "width": info["width"],
            "height": info["height"],
            "duration": info["duration"],
            "delay_ms": delay_ms,
        }


async def confirm(job_manager, filename: str, delay_ms: float) -> dict:
    """Marks the current render as the confirmed synced version. Raises
    ValueError if there's nothing to confirm yet."""
    out_path = _output_path(filename)
    if not os.path.exists(out_path):
        raise ValueError("Apply a delay before confirming - there's no synced render yet.")

    backup_path = _session_backups.pop(filename, None)
    if backup_path and os.path.exists(backup_path):
        _remove_quiet(backup_path)

    job = job_manager.mark_synchronized(filename, delay_ms)
    if job is None:
        raise ValueError(f"'{filename}' isn't a tracked job.")
    return job


async def cancel(job_manager, filename: str) -> None:
    """Called when the Synch UI closes without a (re-)confirmation.
    Restores the pre-session confirmed twin if one was backed up,
    otherwise deletes an unconfirmed render outright."""
    backup_path = _session_backups.pop(filename, None)
    out_path = _output_path(filename)

    if backup_path and os.path.exists(backup_path):
        try:
            if os.path.exists(out_path):
                os.remove(out_path)
            os.replace(backup_path, out_path)
        except OSError:
            pass
        return

    job = job_manager.jobs.get(filename)
    if job and not job.get("synchronized") and os.path.exists(out_path):
        _remove_quiet(out_path)


def _remove_quiet(path: str):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
