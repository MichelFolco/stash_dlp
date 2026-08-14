"""Backend for the "Synchronize Audio" feature.

Workflow: the original video loads first. The user scrubs to a rough
sync point and creates a short (10s) clip from that position - clips
render in well under a second since only the tiny clip needs an audio
re-encode, not the whole file. The user dials in a delay against that
clip (each Apply Sync re-renders just the clip, fast), and can Redo
Clip to throw the clip away and pick a new start point off the full
video. Once the delay looks right, Confirm Sync renders the FULL video
with that delay into a staging file (slower, since the whole audio
track needs re-encoding) without touching any previously-confirmed
twin. The user then Accepts (staging file is promoted to the real
Converted/<stem> slot the Encode Manager also uses - see
find_converted_file - and the clip files are dropped) or Discards
(staging file is dropped and the last clip render is left in place to
keep tweaking).

Because nothing but Accept ever touches the confirmed slot, there's no
need to back up/restore a previously-confirmed twin mid-session the
way earlier versions of this module had to.

All renders keep video as a pure stream copy and only touch the audio
track (adelay/atrim baked into real timestamps, not a container-level
edit-list offset - see the comment in _run_delay_render), which is why
even the full-video render stays fast.

File-lock note: every destructive file op here (os.replace/os.remove)
goes through a small retry loop, because on Windows a file that's
still open for reading (e.g. the browser's <video> element mid-stream
from a previous /api/jobs/stream request) can't be replaced or deleted
until that handle actually closes. The frontend detaches the player
before firing any request that will touch the file it's showing, but
the OS-level close of that handle isn't necessarily instantaneous, so
the retry loop absorbs that race instead of failing outright.
"""
import asyncio
import os

from ffmpeg_encode import probe_basic_info
from procflags import NO_CONSOLE_KWARGS
from settings import get_converted_dir, get_sync_clip_duration
from ytdlp_utils import clean_filename, find_media_file, format_file_size

RENDER_TIMEOUT = 120       # ceiling for a full-video audio re-encode
CLIP_RENDER_TIMEOUT = 30   # clips are tiny; this is generous already
CLIP_DURATION_S = get_sync_clip_duration()

FILE_OP_RETRIES = 12
FILE_OP_RETRY_DELAY = 0.3  # ~3.6s total ceiling before giving up

# Per-filename asyncio locks, so a double-click or a second browser tab
# can't kick off two ffmpeg renders (or overlapping file ops) for the
# same file at once.
_locks: dict = {}


def _lock_for(filename: str) -> asyncio.Lock:
    lock = _locks.get(filename)
    if lock is None:
        lock = asyncio.Lock()
        _locks[filename] = lock
    return lock


# ── Path helpers ─────────────────────────────────────────────────
def _stem(filename: str) -> str:
    return clean_filename(filename)


def clip_src_path(filename: str) -> str:
    """The raw (undelayed) 10s clip, freshly cut from the original
    each time Create Clip / Redo Clip is used."""
    return os.path.join(get_converted_dir(), _stem(filename) + ".sync-clip-src.mp4")


def clip_preview_path(filename: str) -> str:
    """The clip with the currently-dialed-in delay baked in - what
    actually plays back while the user tweaks the delay."""
    return os.path.join(get_converted_dir(), _stem(filename) + ".sync-clip.mp4")


def full_staging_path(filename: str) -> str:
    """The full video re-rendered with the chosen delay, pending
    Accept/Discard. Never the same slot as the confirmed twin, so a
    Discard never risks the previously-confirmed file."""
    return os.path.join(get_converted_dir(), _stem(filename) + ".sync-full-staging.mp4")


def _output_path(filename: str) -> str:
    """The confirmed twin's slot - same one the Encode Manager uses
    for re-encode twins (find_converted_file), so a file can have a
    re-encode twin OR a sync twin, never both."""
    return os.path.join(get_converted_dir(), _stem(filename) + ".mp4")


def resolve_stream_path(filename: str, source: str):
    """Maps a /api/jobs/stream `source` value to a path for the sync
    workflow's in-progress files. Returns None for anything else so
    the caller can fall back to its normal original/converted logic."""
    if source == "sync-clip":
        preview = clip_preview_path(filename)
        if os.path.exists(preview):
            return preview
        return clip_src_path(filename)
    if source == "sync-full":
        return full_staging_path(filename)
    return None


# ── Locked file ops (Windows-sharing-violation-tolerant) ──────────
def _is_lock_error(e: OSError) -> bool:
    if isinstance(e, PermissionError):
        return True
    return getattr(e, "winerror", None) in (32, 33) or e.errno == 13


async def _remove_with_retry(path: str, *, required: bool = False):
    if not path or not os.path.exists(path):
        return
    last_err = None
    for _ in range(FILE_OP_RETRIES):
        try:
            os.remove(path)
            return
        except OSError as e:
            if not _is_lock_error(e):
                if required:
                    raise RuntimeError(f"Couldn't remove '{os.path.basename(path)}': {e}")
                return
            last_err = e
            await asyncio.sleep(FILE_OP_RETRY_DELAY)
    if required:
        raise RuntimeError(
            f"'{os.path.basename(path)}' is still in use (likely still open in the preview "
            f"player) - close the preview and try again. Details: {last_err}"
        )


async def _replace_with_retry(src: str, dst: str):
    last_err = None
    for _ in range(FILE_OP_RETRIES):
        try:
            os.replace(src, dst)
            return
        except OSError as e:
            if not _is_lock_error(e):
                _remove_quiet(src)
                raise RuntimeError(f"Couldn't write '{os.path.basename(dst)}': {e}")
            last_err = e
            await asyncio.sleep(FILE_OP_RETRY_DELAY)
    _remove_quiet(src)
    raise RuntimeError(
        f"'{os.path.basename(dst)}' is still in use (likely still open in the preview "
        f"player) - close the preview and try again. Details: {last_err}"
    )


def _remove_quiet(path: str):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


# ── ffmpeg renders ──────────────────────────────────────────────
async def _run_ffmpeg(cmd, tmp_out_path, timeout):
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            **NO_CONSOLE_KWARGS,
        )
        _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        returncode = proc.returncode
    except FileNotFoundError:
        raise RuntimeError("ffmpeg isn't installed/available on PATH.")
    except asyncio.TimeoutError:
        proc.kill()
        _remove_quiet(tmp_out_path)
        raise RuntimeError("The render timed out.")

    if returncode != 0 or not os.path.exists(tmp_out_path):
        _remove_quiet(tmp_out_path)
        tail = (stderr_bytes or b"").decode("utf-8", errors="ignore").strip().splitlines()
        detail = " | ".join(tail[-3:]) if tail else "Unknown ffmpeg error."
        raise RuntimeError(f"ffmpeg failed: {detail}")


async def _extract_clip(src_path: str, start_s: float, tmp_out_path: str):
    """Cuts a CLIP_DURATION_S clip out of src_path starting at start_s,
    pure stream copy (video+audio untouched) for speed. -ss ahead of
    -i means the cut snaps to the nearest preceding keyframe rather
    than being frame-exact - fine for a sync-preview clip."""
    start_s = max(0.0, start_s)
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start_s:.3f}",
        "-i", src_path,
        "-t", f"{CLIP_DURATION_S:.3f}",
        "-map", "0:v:0",
        "-map", "0:a:0",
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        "-f", "mp4", tmp_out_path,
    ]
    await _run_ffmpeg(cmd, tmp_out_path, CLIP_RENDER_TIMEOUT)


async def _run_delay_render(src_path: str, tmp_out_path: str, delay_ms: float, timeout: float):
    """Video is always a pure stream copy - it's never touched, which
    is what keeps this fast even for full-length renders. Audio timing
    is baked directly into real timestamps via a filter (not a
    container-level edit-list offset from dual-input -itsoffset, which
    some players/tools don't honor), so it needs a light re-encode -
    cheap even for long files since audio codecs are trivial next to
    video.

    Positive delay_ms (audio leads, needs delaying): pad silence onto
    the front of the audio track with adelay. -shortest then trims the
    resulting excess audio tail back down to video's length, since
    video should stay full-length.

    Negative delay_ms (audio lags, needs advancing): trim that much
    off the front of the audio track and reset its timestamps to start
    at 0. No -shortest here - video should still play in full even
    though audio now runs out slightly early at the end.
    """
    delay_s = delay_ms / 1000.0
    cmd = [
        "ffmpeg", "-y",
        "-i", src_path,
        "-map", "0:v:0",
        "-map", "0:a:0",
        "-c:v", "copy",
    ]
    if delay_ms > 0:
        cmd += [
            "-af", f"adelay=delays={delay_ms:.0f}:all=1",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
        ]
    elif delay_ms < 0:
        cmd += [
            "-af", f"atrim=start={abs(delay_s):.3f},asetpts=PTS-STARTPTS",
            "-c:a", "aac", "-b:a", "192k",
        ]
    else:
        cmd += ["-c:a", "copy"]
    cmd += ["-avoid_negative_ts", "make_zero", "-f", "mp4", tmp_out_path]

    await _run_ffmpeg(cmd, tmp_out_path, timeout)


def _require_completed_video_job(job_manager, filename: str):
    job = job_manager.jobs.get(filename)
    if not job or job.get("status") != "DONE" or job.get("is_audio"):
        raise ValueError(f"'{filename}' isn't a completed video.")
    return job


async def _probe_result(path: str, delay_ms: float, extra: dict) -> dict:
    info = await probe_basic_info(path)
    size_bytes = os.path.getsize(path)
    result = {
        "output_filename": os.path.basename(path),
        "size_label": format_file_size(size_bytes),
        "width": info["width"],
        "height": info["height"],
        "duration": info["duration"],
        "delay_ms": delay_ms,
    }
    result.update(extra)
    return result


# ── Public lifecycle ────────────────────────────────────────────
async def create_clip(job_manager, filename: str, start_seconds: float, delay_ms: float = 0.0) -> dict:
    """Cuts a fresh clip from the original video at start_seconds. If
    delay_ms is non-zero, the delay is baked in immediately so Redo
    Clip -> Create Clip at a new point keeps whatever delay was
    already dialed in; otherwise the raw clip is served as-is."""
    _require_completed_video_job(job_manager, filename)
    media_path = find_media_file(filename)
    if not media_path:
        raise ValueError(f"Couldn't find the file for '{filename}'.")

    async with _lock_for(filename):
        clip_src = clip_src_path(filename)
        clip_preview = clip_preview_path(filename)

        tmp_src = clip_src + ".tmp"
        await _extract_clip(media_path, start_seconds, tmp_src)

        # Drop any stale preview from a previous clip before installing
        # the new source, so nothing can serve a delay baked against
        # the wrong clip.
        await _remove_with_retry(clip_preview, required=True)
        await _replace_with_retry(tmp_src, clip_src)

        play_path = clip_src
        if delay_ms:
            tmp_preview = clip_preview + ".tmp"
            await _run_delay_render(clip_src, tmp_preview, delay_ms, CLIP_RENDER_TIMEOUT)
            await _replace_with_retry(tmp_preview, clip_preview)
            play_path = clip_preview

        return await _probe_result(play_path, delay_ms, {"clip_start": max(0.0, start_seconds)})


async def apply_clip_delay(job_manager, filename: str, delay_ms: float) -> dict:
    """Re-renders the clip preview with a new delay from the cached
    (undelayed) clip source - fast, since only the 10s clip's audio is
    touched."""
    clip_src = clip_src_path(filename)
    if not os.path.exists(clip_src):
        raise ValueError("Create a clip before applying a sync delay.")

    async with _lock_for(filename):
        clip_preview = clip_preview_path(filename)
        tmp_preview = clip_preview + ".tmp"
        await _run_delay_render(clip_src, tmp_preview, delay_ms, CLIP_RENDER_TIMEOUT)
        await _replace_with_retry(tmp_preview, clip_preview)
        return await _probe_result(clip_preview, delay_ms, {})


async def redo_clip(job_manager, filename: str) -> None:
    """Drops the current clip so the frontend can reload the full
    original video and pick a new start point."""
    async with _lock_for(filename):
        await _remove_with_retry(clip_preview_path(filename), required=True)
        await _remove_with_retry(clip_src_path(filename), required=True)


async def render_full(job_manager, filename: str, delay_ms: float) -> dict:
    """Confirm Sync: renders the WHOLE video with delay_ms into a
    staging file, never touching any previously-confirmed twin."""
    _require_completed_video_job(job_manager, filename)
    media_path = find_media_file(filename)
    if not media_path:
        raise ValueError(f"Couldn't find the file for '{filename}'.")

    async with _lock_for(filename):
        staging = full_staging_path(filename)
        tmp_staging = staging + ".tmp"
        await _run_delay_render(media_path, tmp_staging, delay_ms, RENDER_TIMEOUT)
        await _replace_with_retry(tmp_staging, staging)
        return await _probe_result(staging, delay_ms, {})


async def accept(job_manager, filename: str, delay_ms: float) -> dict:
    """Promotes the staged full render to the confirmed slot, marks the
    job synchronized, and drops the clip files - ends the session."""
    staging = full_staging_path(filename)
    if not os.path.exists(staging):
        raise ValueError("Confirm the sync before accepting - there's no full render yet.")

    async with _lock_for(filename):
        out_path = _output_path(filename)
        await _remove_with_retry(out_path, required=True)
        await _replace_with_retry(staging, out_path)
        await _remove_with_retry(clip_preview_path(filename), required=True)
        await _remove_with_retry(clip_src_path(filename), required=True)

    job = job_manager.mark_synchronized(filename, delay_ms)
    if job is None:
        raise ValueError(f"'{filename}' isn't a tracked job.")
    return job


async def discard_full(job_manager, filename: str) -> None:
    """Drops the staged full render; the clip files are left alone so
    the frontend can reload the last clip preview and keep tweaking."""
    async with _lock_for(filename):
        await _remove_with_retry(full_staging_path(filename), required=True)


async def cancel(job_manager, filename: str) -> None:
    """Called when the Sync UI closes without accepting. Cleans up
    every unconfirmed file for this session - the confirmed slot is
    never touched here since only accept() ever writes to it."""
    async with _lock_for(filename):
        await _remove_with_retry(full_staging_path(filename))
        await _remove_with_retry(clip_preview_path(filename))
        await _remove_with_retry(clip_src_path(filename))
