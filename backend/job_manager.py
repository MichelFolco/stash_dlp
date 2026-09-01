"""In-memory job manager. Each active download is one asyncio task reading
a yt-dlp subprocess's stdout, matching the same progress regex the desktop
app's DownloadWorker used. Progress/completion is broadcast to all
connected WebSocket clients so every open browser tab stays in sync.
"""
import asyncio
import json
import os
import re
import shutil
import uuid
from urllib.parse import urlparse
from typing import Dict, Optional

from config import RES_FORMATS, AUDIO_ONLY_KEY
from ffmpeg_encode import probe_basic_info
from procflags import NO_CONSOLE_KWARGS
from settings import get_download_prefs, get_save_dir, get_target_dir, get_ytdlp_args, get_converted_dir
from storage import load_saved_queue, save_queue_to_disk, write_to_history_log
from thumbnails import thumbnail_path_for
from ytdlp_utils import (
    clean_filename,
    format_file_size,
    get_downloaded_file_size,
    find_media_file,
    find_converted_file,
    list_converted_entries,
    find_converted_twin_path,
    has_converted_twin,
    is_audio_file,
    get_domain,
    split_args_string,
)

PROGRESS_RE = re.compile(
    r"\[download\]\s+([\d.]+)%\s+of\s+~?\s*([\d.]+)\s*(\w+)\s+at\s+([\d.]+)\s*([\w/]+)\s+ETA\s+(\d+):(\d+)"
)

# Max downloads running at once FOR A SINGLE PLAYLIST BATCH - each
# playlist queued gets its own independent cap, so two playlists queued
# at the same time run up to 2x this many concurrent downloads total.
# Manually-pasted single URLs are unaffected (never throttled).
PLAYLIST_CONCURRENCY = 3


class NeedsDecisionError(Exception):
    """Raised by move_to_target() when the file being moved has a
    re-encoded OR synchronized twin sitting in Converted/ and the caller
    hasn't said which version (original/reencoded) should actually go to
    the target folder. Carries the info the frontend needs to render its
    Transfer Original / Transfer Converted / Cancel prompt - info["kind"]
    ("reencoded" or "synchronized") tells the frontend which label to use
    for the second card, since the prompt itself is shared."""
    def __init__(self, info: dict):
        super().__init__("A converted version of this file exists - pick which one to transfer.")
        self.info = info


class ConnectionManager:
    def __init__(self):
        self.active: list = []

    async def connect(self, ws):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_text(json.dumps(message))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


class JobManager:
    def __init__(self, connections: "ConnectionManager | None" = None):
        self.jobs: Dict[str, dict] = {}  # filename -> job state dict, insertion order preserved
        self.saved_queue: dict = load_saved_queue()
        self.connections = connections if connections is not None else ConnectionManager()
        self._processes: Dict[str, asyncio.subprocess.Process] = {}
        self._cancelled: set = set()

    # ── Bootstrapping ────────────────────────────────────────────
    async def seed_from_filesystem(self, done_jobs: list, replace: bool = False):
        """Populate self.jobs with jobs discovered by filesystem_scan.
        With replace=True (used when the download folder changes, and on
        every periodic refresh poll), any previously-tracked completed
        jobs from the old folder are dropped first. This is a denylist,
        not an allowlist: only DONE/ERROR/CANCELLED are dropped, since
        this scan is authoritative over those (it just re-derived them
        from what's actually on disk) - every other status, including
        ones that don't exist yet, is kept by default rather than
        needing to be remembered here. DOWNLOADING and QUEUED are the
        two that matter today - neither has settled into a file on disk
        yet for this scan to find - but a future status shouldn't need
        a matching edit in this one place just to not get silently
        wiped on the next refresh tick, the way QUEUED almost was.

        Media metadata (resolution/duration/filetype) is cached in
        queue.json once probed, so this only pays the ffprobe cost for
        files that haven't been seen before (new drops, or the first
        scan after upgrading from a version that didn't track this)."""
        if replace:
            self.jobs = {
                filename: job
                for filename, job in self.jobs.items()
                if job["status"] not in ("DONE", "ERROR", "CANCELLED")
            }

        disk_queue = load_saved_queue()
        queue_dirty = False

        # One Converted/ listing for the whole batch, instead of one
        # os.listdir() per job via find_converted_file() - this loop
        # runs on every /api/refresh call (including the frontend's 5s
        # auto-refresh poll), so turning that into N listdirs per tick
        # would scale badly with queue size, and worse on a networked
        # Converted/ path.
        converted_entries = list_converted_entries()

        for job in done_jobs:
            filename = job["filename"]
            width = job.get("width", 0)
            height = job.get("height", 0)
            duration = job.get("duration", 0)
            ext = job.get("ext", "")
            video_codec = job.get("video_codec", "")
            audio_codec = job.get("audio_codec", "")
            is_audio = job.get("is_audio", False)

            # Probe if we're missing the extension, or if we don't have any
            # real media info yet. The latter case covers a prior probe
            # attempt that failed (flaky ffprobe call, file still being
            # copied/synced onto a network drive, etc.) - without this,
            # a failed attempt's zeros would look identical to a
            # successfully-probed audio file's, and we'd never retry.
            needs_probe = not ext or not duration or (not is_audio and not (width and height))

            if needs_probe:
                media_path = find_media_file(filename, job.get("save_dir"))
                if media_path:
                    if not ext:
                        ext = os.path.splitext(media_path)[1].lstrip(".").upper()
                    try:
                        probed = await probe_basic_info(media_path)
                    except Exception:
                        probed = {"width": 0, "height": 0, "duration": 0.0, "video_codec": "", "audio_codec": ""}
                    new_width, new_height, new_duration = probed["width"], probed["height"], probed["duration"]
                    new_video_codec = probed.get("video_codec", "")
                    new_audio_codec = probed.get("audio_codec", "")

                    # Only treat this as a successful probe - and only
                    # permanently cache it - if ffprobe actually returned
                    # something usable. Otherwise leave width/height/duration
                    # uncached so the next scan (e.g. pressing Refresh) tries
                    # probing again instead of getting stuck at 0x0 forever.
                    probed_ok = bool(new_duration or new_width or new_height or new_video_codec or new_audio_codec)

                    if not probed_ok:
                        # A brand-new file (e.g. manually pasted into the
                        # download folder) can still be mid-copy the instant
                        # it's first noticed - the OS already shows it in the
                        # folder listing, but ffprobe can't read a complete
                        # header yet. That used to require the user to press
                        # Refresh a second time once the copy settled. Give
                        # it one short retry before giving up, so a single
                        # Refresh is enough in the common case.
                        await asyncio.sleep(0.4)
                        try:
                            probed = await probe_basic_info(media_path)
                        except Exception:
                            probed = {"width": 0, "height": 0, "duration": 0.0, "video_codec": "", "audio_codec": ""}
                        new_width, new_height, new_duration = probed["width"], probed["height"], probed["duration"]
                        new_video_codec = probed.get("video_codec", "")
                        new_audio_codec = probed.get("audio_codec", "")
                        probed_ok = bool(new_duration or new_width or new_height or new_video_codec or new_audio_codec)
                    if probed_ok:
                        width, height, duration = new_width, new_height, new_duration
                        video_codec, audio_codec = new_video_codec, new_audio_codec
                        if filename in disk_queue:
                            disk_queue[filename].update({
                                "width": width, "height": height,
                                "duration": duration, "ext": ext,
                                "video_codec": video_codec, "audio_codec": audio_codec,
                            })
                            queue_dirty = True
                    elif filename in disk_queue and disk_queue[filename].get("ext") != ext:
                        # Still cache the extension so the ledger's file
                        # path displays correctly, without falsely marking
                        # the media info as resolved.
                        disk_queue[filename]["ext"] = ext
                        queue_dirty = True

            has_twin = has_converted_twin(filename, converted_entries)
            twin_size = 0
            twin_size_label = ""
            twin_width = 0
            twin_height = 0
            if has_twin:
                twin_path = find_converted_twin_path(filename, converted_entries)
                if twin_path:
                    try:
                        twin_stat = os.stat(twin_path)
                        twin_size = twin_stat.st_size
                        twin_size_label = format_file_size(twin_size)
                        # Resolution is the only part of this that needs
                        # ffprobe (size is a free stat()), and this loop
                        # runs on every refresh poll - so cache it in
                        # disk_queue keyed off the twin's own mtime+size,
                        # reprobing only when the twin file has actually
                        # changed (e.g. a fresh re-encode replaced it).
                        cached_twin = disk_queue.get(filename, {}).get("twin_cache")
                        if (
                            cached_twin
                            and cached_twin.get("mtime") == twin_stat.st_mtime
                            and cached_twin.get("size") == twin_size
                        ):
                            twin_width = cached_twin.get("width", 0)
                            twin_height = cached_twin.get("height", 0)
                        else:
                            try:
                                probed_twin = await probe_basic_info(twin_path)
                            except Exception:
                                probed_twin = {"width": 0, "height": 0}
                            twin_width = probed_twin.get("width", 0)
                            twin_height = probed_twin.get("height", 0)
                            if filename in disk_queue:
                                disk_queue[filename]["twin_cache"] = {
                                    "mtime": twin_stat.st_mtime, "size": twin_size,
                                    "width": twin_width, "height": twin_height,
                                }
                                queue_dirty = True
                    except OSError:
                        pass

            self.jobs[filename] = {
                "filename": filename,
                "url": job["url"],
                "res_cap": job["res_cap"],
                "status": job["status"],
                "file_size": job.get("file_size", ""),
                "is_audio": job.get("is_audio", False),
                "playback_position": job.get("playback_position", 0),
                "fully_played": job.get("fully_played", False),
                "width": width,
                "height": height,
                "duration": duration,
                "ext": ext,
                "video_codec": video_codec,
                "audio_codec": audio_codec,
                "pct": 100,
                "total": "",
                "speed": "",
                "eta": "",
                "source_type": disk_queue.get(filename, {}).get("source_type", ""),
                "source_path": disk_queue.get(filename, {}).get("source_path", ""),
                "stash_scene_id": disk_queue.get(filename, {}).get("stash_scene_id", ""),
                "stash_scene_url": disk_queue.get(filename, {}).get("stash_scene_url", ""),
                "stash_tag_id": disk_queue.get(filename, {}).get("stash_tag_id"),
                "stash_tag_name": disk_queue.get(filename, {}).get("stash_tag_name"),
                "stash_tags": disk_queue.get(filename, {}).get("stash_tags", []),
                "synchronized": disk_queue.get(filename, {}).get("synchronized", False),
                "audio_delay_ms": disk_queue.get(filename, {}).get("audio_delay_ms", 0),
                # Direct filesystem check for a twin file sitting in
                # Converted/ - independent of isReencoded()'s in-memory
                # Encode Manager history and the persisted "synchronized"
                # flag, so it still catches a twin after a server restart
                # (or one dropped into Converted/ by hand). Checked
                # against the single listing above, not a fresh listdir
                # per job.
                "has_twin": has_twin,
                "twin_size": twin_size,
                "twin_size_label": twin_size_label,
                "twin_width": twin_width,
                "twin_height": twin_height,
                "save_dir": disk_queue.get(filename, {}).get("save_dir", get_save_dir()),
            }

        if queue_dirty:
            save_queue_to_disk(disk_queue)

        self.saved_queue = load_saved_queue()

    def snapshot(self) -> list:
        """Most-recently-started first, matching the desktop ledger's
        insertOnTop behavior for active jobs, completed jobs following."""
        return list(self.jobs.values())[::-1]

    # ── Starting a job ───────────────────────────────────────────
    async def start_job(self, url: str, filename: str, res_cap: str, original_pasted_url: str):
        if filename in self.jobs and self.jobs[filename]["status"] == "DOWNLOADING":
            return  # already running, ignore duplicate submit

        job = {
            "filename": filename,
            "url": url,
            "res_cap": res_cap,
            "status": "DOWNLOADING",
            "file_size": "",
            "is_audio": res_cap == AUDIO_ONLY_KEY,  # confirmed for real once the file lands
            "playback_position": 0,
            "fully_played": False,
            "width": 0,
            "height": 0,
            "duration": 0,
            "ext": "",
            "video_codec": "",
            "audio_codec": "",
            "pct": 0,
            "total": "",
            "speed": "",
            "eta": "",
            "synchronized": False,
            "audio_delay_ms": 0,
            "has_twin": False,  # nothing in Converted/ yet - a download just started
            "twin_size": 0,
            "twin_size_label": "",
            "twin_width": 0,
            "twin_height": 0,
            "save_dir": get_save_dir(),
        }
        self.jobs[filename] = job

        self.saved_queue[filename] = {
            "url": url,
            "res_cap": res_cap,
            "status": "DOWNLOADING",
            "original_input_url": original_pasted_url,
            "save_dir": job["save_dir"],
        }
        save_queue_to_disk(self.saved_queue)

        await self.connections.broadcast({"type": "job_added", "job": job})

        asyncio.create_task(self._run_download(job, original_pasted_url))

    async def _run_download(self, job: dict, log_url: str):
        filename = job["filename"]
        save_dir = job.get("save_dir") or get_save_dir()
        os.makedirs(save_dir, exist_ok=True)
        out_path = os.path.join(save_dir, filename + ".%(ext)s")
        is_audio_only = job["res_cap"] == AUDIO_ONLY_KEY

        status = await self._attempt_ytdlp(job, job["url"], out_path, is_audio_only, filename)

        # Default method failed - rather than leaving a failed entry
        # sitting in the ledger, pull the job out entirely and hand
        # things back to the frontend so it can run the same M3U
        # sniffing flow the user would trigger manually (message in the
        # input box, sniff, then let the user confirm/edit the title
        # before it's resubmitted as a fresh job). Gated behind a
        # settings toggle - some users would rather just see the plain
        # ERROR card than have every failure kick off a sniff attempt.
        if status == "ERROR" and filename not in self._cancelled and get_download_prefs()["auto_m3u_retry"]:
            await self._abandon_for_m3u_retry(job, log_url)
            return

        self._cancelled.discard(filename)
        await self._finalize_job(job, status, log_url)

    # ── Playlist batches ────────────────────────────────────────────
    async def start_playlist_batch(self, entries: list, res_cap: str, save_dir: str | None = None, number_titles: bool = False) -> dict:
        """Queues every entry from a detected playlist/channel as its own
        ledger item with status QUEUED, then lets them start downloading
        PLAYLIST_CONCURRENCY at a time via a semaphore scoped to just
        this batch (a second playlist queued later gets its own separate
        semaphore/cap, entirely independent of this one). Returns
        {'queued': [filenames]} - entries that collide with an
        already-downloading filename are silently skipped, same dedupe
        behavior start_job already applies to a single submission."""
        # Snapshot the destination once for the entire playlist. Changing the
        # app's download folder after this point must not redirect queued items.
        save_dir = save_dir or get_save_dir()
        semaphore = asyncio.Semaphore(max(1, PLAYLIST_CONCURRENCY))
        queued = []
        for index, entry in enumerate(entries, start=1):
            title = entry.get("title", "")
            if number_titles:
                title = f"{index:02d}-{title}"
            filename = clean_filename(title)
            url = entry.get("url", "")
            if not filename or not url:
                continue
            if self.jobs.get(filename, {}).get("status") in ("DOWNLOADING", "QUEUED"):
                continue
            await self._enqueue_playlist_item(url, filename, res_cap, semaphore, save_dir)
            queued.append(filename)
        return {"queued": queued}

    async def _enqueue_playlist_item(self, url: str, filename: str, res_cap: str, semaphore: asyncio.Semaphore, save_dir: str) -> None:
        job = {
            "filename": filename,
            "url": url,
            "res_cap": res_cap,
            "status": "QUEUED",
            "file_size": "",
            "is_audio": res_cap == AUDIO_ONLY_KEY,
            "playback_position": 0,
            "fully_played": False,
            "width": 0,
            "height": 0,
            "duration": 0,
            "ext": "",
            "video_codec": "",
            "audio_codec": "",
            "pct": 0,
            "total": "",
            "speed": "",
            "eta": "",
            "synchronized": False,
            "audio_delay_ms": 0,
            "has_twin": False,
            "twin_size": 0,
            "twin_size_label": "",
            "twin_width": 0,
            "twin_height": 0,
            "save_dir": save_dir,
        }
        self.jobs[filename] = job

        self.saved_queue[filename] = {
            "url": url,
            "res_cap": res_cap,
            "status": "QUEUED",
            "original_input_url": url,
            "save_dir": save_dir,
        }
        save_queue_to_disk(self.saved_queue)

        await self.connections.broadcast({"type": "job_added", "job": job})

        asyncio.create_task(self._run_queued_download(job, url, semaphore))

    async def _run_queued_download(self, job: dict, log_url: str, semaphore: asyncio.Semaphore) -> None:
        filename = job["filename"]
        async with semaphore:
            # Cancelled while waiting for a slot, or superseded by a
            # retry that replaced this filename's job dict in the
            # meantime - either way, this stale wait is done and
            # shouldn't start a download.
            if filename in self._cancelled or self.jobs.get(filename) is not job:
                self._cancelled.discard(filename)
                return

            job["status"] = "DOWNLOADING"
            if filename in self.saved_queue:
                self.saved_queue[filename]["status"] = "DOWNLOADING"
                save_queue_to_disk(self.saved_queue)
            await self.connections.broadcast({
                "type": "job_status", "filename": filename, "status": "DOWNLOADING",
            })
            await self._run_download(job, log_url)

    async def _abandon_for_m3u_retry(self, job: dict, log_url: str) -> None:
        filename = job["filename"]
        self.jobs.pop(filename, None)
        self.saved_queue.pop(filename, None)
        save_queue_to_disk(self.saved_queue)

        await self.connections.broadcast({"type": "job_deleted", "filename": filename})
        await self.connections.broadcast({
            "type": "download_failed_retry_m3u",
            "filename": filename,
            "url": job["url"],
            "res_cap": job["res_cap"],
            "original_pasted_url": log_url,
        })

    async def _attempt_ytdlp(self, job: dict, url: str, out_path: str, is_audio_only: bool, filename: str) -> str:
        """Runs a single yt-dlp attempt against `url` and returns
        'DONE' / 'ERROR' / 'CANCELLED'."""
        # Global default args (throttling knobs, --cookies-from-browser,
        # etc.) plus any rule saved for this URL's domain (site-specific
        # fixes like a TikTok --extractor-args workaround), appended
        # AFTER the built-in flags below so a single-value override
        # (e.g. -f) in the user's args wins without yt-dlp erroring on
        # a repeated flag; harmless boolean flags simply appear twice.
        ytdlp_args = get_ytdlp_args()
        extra_args = split_args_string(ytdlp_args["default_args"])
        extra_args += split_args_string(ytdlp_args["domain_args"].get(get_domain(url), ""))

        try:
            if is_audio_only:
                proc = await asyncio.create_subprocess_exec(
                    "yt-dlp",
                    "-f", "bestaudio/best",
                    "--extract-audio",
                    "--audio-format", "mp3",
                    "--audio-quality", "0",  # best VBR quality
                    "--no-playlist",
                    "--newline",
                    "--progress",
                    # yt-dlp only honors --paths TYPE:... when the main
                    # -o outtmpl is relative; ours below is absolute, so
                    # --paths would silently be ignored and the thumbnail
                    # would land beside the media file instead. Giving the
                    # "thumbnail" type its own absolute -o instead is the
                    # form yt-dlp always honors, and sends it straight to
                    # the central library_data thumbnail cache.
                    "--write-thumbnail",
                    "--convert-thumbnails", "jpg",
                    "-o", f"thumbnail:{os.path.splitext(thumbnail_path_for(filename))[0]}.%(ext)s",
                    *extra_args,
                    "-o", out_path,
                    url,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    **NO_CONSOLE_KWARGS,
                )
            else:
                fmt = RES_FORMATS.get(job["res_cap"], RES_FORMATS["Best"])
                proc = await asyncio.create_subprocess_exec(
                    "yt-dlp",
                    "-f", fmt,
                    "--merge-output-format", "mp4",
                    "--no-playlist",
                    "--newline",
                    "--progress",
                    # See comment in the is_audio_only branch above.
                    "--write-thumbnail",
                    "--convert-thumbnails", "jpg",
                    "-o", f"thumbnail:{os.path.splitext(thumbnail_path_for(filename))[0]}.%(ext)s",
                    *extra_args,
                    "-o", out_path,
                    url,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    **NO_CONSOLE_KWARGS,
                )
        except Exception:
            # yt-dlp missing/unrunnable.
            return "ERROR"

        self._processes[filename] = proc

        try:
            async for raw_line in proc.stdout:
                line = raw_line.decode("utf-8", errors="ignore").strip()
                await self._parse_line(job, line)
        except Exception:
            pass

        try:
            returncode = await proc.wait()
        except Exception:
            returncode = -1

        self._processes.pop(filename, None)

        if filename in self._cancelled:
            return "CANCELLED"
        elif returncode == 0:
            return "DONE"
        else:
            return "ERROR"

    async def _parse_line(self, job: dict, line: str):
        m = PROGRESS_RE.search(line)
        if m:
            pct = m.group(1)
            total = f"{float(m.group(2)):.1f} {m.group(3)}"
            raw_speed = float(m.group(4))
            unit = m.group(5).lower()

            if "m" in unit:
                speed_str = f"{raw_speed:.1f} MB/s"
            elif "k" in unit:
                speed_str = f"{raw_speed:.1f} KB/s"
            else:
                speed_str = f"{raw_speed:.1f} B/s"

            secs = int(m.group(6)) * 60 + int(m.group(7))
            eta_str = f"{secs // 60}m {secs % 60}s" if secs >= 60 else f"{secs}s"

            job["pct"] = pct
            job["total"] = total
            job["speed"] = speed_str
            job["eta"] = eta_str

            await self.connections.broadcast({
                "type": "job_progress",
                "filename": job["filename"],
                "pct": pct, "total": total, "speed": speed_str, "eta": eta_str,
            })
        elif "Merging" in line or "[Merger]" in line:
            job["pct"] = "100"
            job["speed"] = "mrg"
            await self.connections.broadcast({
                "type": "job_progress",
                "filename": job["filename"],
                "pct": "100", "total": "", "speed": "mrg", "eta": "",
            })

    async def _finalize_job(self, job: dict, status: str, log_url: str):
        filename = job["filename"]
        file_size_str = ""
        is_audio = job.get("is_audio", False)
        width, height, duration, ext = 0, 0, 0, ""
        video_codec, audio_codec = "", ""
        if status == "DONE":
            size_bytes = get_downloaded_file_size(filename, job.get("save_dir"))
            if size_bytes is not None:
                file_size_str = format_file_size(size_bytes)
            media_path = find_media_file(filename, job.get("save_dir"))
            if media_path:
                is_audio = is_audio_file(media_path)
                ext = os.path.splitext(media_path)[1].lstrip(".").upper()
                try:
                    probed = await probe_basic_info(media_path)
                    width, height, duration = probed["width"], probed["height"], probed["duration"]
                    video_codec = probed.get("video_codec", "")
                    audio_codec = probed.get("audio_codec", "")
                except Exception:
                    pass
            self._relocate_downloaded_thumbnail(filename, job.get("save_dir"))

        job["status"] = status
        job["file_size"] = file_size_str
        job["is_audio"] = is_audio
        job["width"] = width
        job["height"] = height
        job["duration"] = duration
        job["ext"] = ext
        job["video_codec"] = video_codec
        job["audio_codec"] = audio_codec

        if filename in self.saved_queue:
            self.saved_queue[filename]["status"] = status
            if file_size_str:
                self.saved_queue[filename]["file_size"] = file_size_str
            self.saved_queue[filename]["is_audio"] = is_audio
            self.saved_queue[filename]["width"] = width
            self.saved_queue[filename]["height"] = height
            self.saved_queue[filename]["duration"] = duration
            self.saved_queue[filename]["ext"] = ext
            self.saved_queue[filename]["video_codec"] = video_codec
            self.saved_queue[filename]["audio_codec"] = audio_codec
            save_queue_to_disk(self.saved_queue)

        write_to_history_log(
            filename,
            log_url or self.saved_queue.get(filename, {}).get("url", ""),
            status,
        )

        await self.connections.broadcast({
            "type": "job_finished",
            "filename": filename,
            "status": status,
            "file_size": file_size_str,
            "is_audio": is_audio,
            "width": width,
            "height": height,
            "duration": duration,
            "ext": ext,
            "video_codec": video_codec,
            "audio_codec": audio_codec,
        })

    def _relocate_downloaded_thumbnail(self, filename: str, save_dir: str | None = None) -> None:
        """Compatibility fallback for thumbnails written by older yt-dlp
        configurations.

        New downloads are directed straight into the central library_data
        thumbnail cache via --paths thumbnail:..., so this normally has
        nothing to do. It only moves a legacy <filename>.jpg left beside
        the media file by an older yt-dlp invocation.
        """
        source = os.path.join(save_dir or get_save_dir(), filename + ".jpg")
        if not os.path.exists(source):
            return
        dest = thumbnail_path_for(filename)
        try:
            if os.path.exists(dest):
                os.remove(dest)
            os.replace(source, dest)
        except OSError:
            pass

    # ── Playback position tracking ────────────────────────────────
    def set_playback_position(self, filename: str, position, completed: bool = False) -> bool:
        """Persists how far into a file playback got, so it can resume
        there next time - survives the browser closing/crashing or the
        server restarting, since it's written straight to queue.json.

        completed=True additionally sets fully_played, a separate sticky
        flag (never cleared by this method) marking the file as having
        been watched/listened to the end at least once. It's tracked
        apart from playback_position because that field gets reset to 0
        once a file finishes (so the next play starts over instead of
        immediately resuming at the very end) - without a separate flag,
        a finished file and a never-started one would be indistinguishable
        once playback_position is back to 0. A partial rewatch afterwards
        still updates playback_position normally; fully_played only ever
        moves False -> True, here, never back."""
        job = self.jobs.get(filename)
        if not job:
            return False
        try:
            position = max(0.0, float(position))
        except (TypeError, ValueError):
            return False

        job["playback_position"] = position
        if completed:
            job["fully_played"] = True
        if filename in self.saved_queue:
            self.saved_queue[filename]["playback_position"] = position
            if completed:
                self.saved_queue[filename]["fully_played"] = True
            save_queue_to_disk(self.saved_queue)
        return True

    def mark_synchronized(self, filename: str, delay_ms) -> "Optional[dict]":
        """Marks a completed download as having a confirmed Synchronize
        Audio twin in Converted/, persisting the delay used - survives
        restarts the same way playback_position does. Returns the
        updated job, or None if there's no such tracked job."""
        job = self.jobs.get(filename)
        if not job:
            return None
        try:
            delay_ms = float(delay_ms)
        except (TypeError, ValueError):
            delay_ms = 0.0

        job["synchronized"] = True
        job["audio_delay_ms"] = delay_ms
        if filename in self.saved_queue:
            self.saved_queue[filename]["synchronized"] = True
            self.saved_queue[filename]["audio_delay_ms"] = delay_ms
            save_queue_to_disk(self.saved_queue)
        return job

    # ── Retrying a failed/cancelled download ───────────────────────
    async def retry_job(self, filename: str) -> Optional[dict]:
        """Re-runs a failed or cancelled download using the same URL and
        resolution it was originally queued with. Reuses the same
        ledger slot (start_job overwrites self.jobs[filename] in place),
        so the card stays put rather than duplicating. Returns the
        refreshed job dict, or None if there was nothing retryable."""
        job = self.jobs.get(filename)
        if not job or job["status"] not in ("ERROR", "CANCELLED"):
            return None

        queue_entry = self.saved_queue.get(filename, {})
        url = job.get("url") or queue_entry.get("url", "")
        if not url:
            return None
        res_cap = job.get("res_cap") or queue_entry.get("res_cap", "720p")
        original_pasted_url = queue_entry.get("original_input_url", url)

        await self.start_job(url, filename, res_cap, original_pasted_url)
        return self.jobs[filename]

    # ── Cancelling ────────────────────────────────────────────────
    async def cancel_job(self, filename: str) -> bool:
        proc = self._processes.get(filename)
        if proc is not None:
            self._cancelled.add(filename)
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return True

        job = self.jobs.get(filename)
        if job is not None and job.get("status") == "QUEUED":
            # Still waiting on its playlist batch's concurrency slot -
            # no process to kill yet. Flip it to CANCELLED in place
            # (rather than waiting for a slot that might be a long time
            # coming) so it stays retryable via the same Retry action as
            # any other cancelled download; the still-pending semaphore
            # wait (see _run_queued_download) checks self._cancelled once
            # its turn comes up and quietly no-ops instead of downloading.
            self._cancelled.add(filename)
            job["status"] = "CANCELLED"
            if filename in self.saved_queue:
                self.saved_queue[filename]["status"] = "CANCELLED"
                save_queue_to_disk(self.saved_queue)
            await self.connections.broadcast({
                "type": "job_finished",
                "filename": filename,
                "status": "CANCELLED",
                "file_size": "",
                "is_audio": job.get("is_audio", False),
                "width": 0, "height": 0, "duration": 0, "ext": "",
                "video_codec": "", "audio_codec": "",
            })
            return True

        return False

    # ── Deleting a completed job's file ───────────────────────────
    def delete_job(self, filename: str) -> bool:
        """Deletes the media file (and any thumbnail) from disk, and
        drops the job from the ledger/queue.json entirely."""
        media_path = find_media_file(filename)
        if media_path:
            try:
                os.remove(media_path)
            except OSError:
                pass

        thumb_path = thumbnail_path_for(filename)
        if os.path.exists(thumb_path):
            try:
                os.remove(thumb_path)
            except OSError:
                pass

        self.jobs.pop(filename, None)
        if filename in self.saved_queue:
            del self.saved_queue[filename]
            save_queue_to_disk(self.saved_queue)
        return True

    # ── Renaming a completed job's file ───────────────────────────
    def rename_job(self, filename: str, new_name_raw: str) -> str:
        """Renames the media file (and thumbnail) on disk, and moves the
        job/queue entry to the new key. Returns the final clean name.
        Raises ValueError if the new name is invalid or already taken."""
        existing = self.jobs.get(filename)
        if existing and existing.get("stash_tag_name"):
            raise ValueError(
                f"Renaming is disabled for items tagged \"{existing['stash_tag_name']}\" from a Stash tag check."
            )

        new_name = clean_filename(new_name_raw)
        if not new_name:
            raise ValueError("New name can't be empty.")
        if new_name == filename:
            return filename
        if new_name in self.jobs:
            raise ValueError(f"'{new_name}' is already used by another item.")

        media_path = find_media_file(filename)
        save_dir = get_save_dir()

        # If this job has a twin in Converted/, keep the twin's stem in sync
        # with the renamed source. Preserve the twin's actual extension.
        twin_path = find_converted_file(filename)
        twin_new_path = None
        if twin_path:
            twin_ext = os.path.splitext(twin_path)[1]
            twin_new_path = os.path.join(get_converted_dir(), new_name + twin_ext)
            if os.path.normcase(os.path.abspath(twin_path)) != os.path.normcase(os.path.abspath(twin_new_path)) and os.path.exists(twin_new_path):
                raise ValueError(f"A twin named '{new_name}{twin_ext}' already exists in Converted/.")

        if media_path:
            ext = os.path.splitext(media_path)[1]
            new_media_path = os.path.join(save_dir, new_name + ext)
            if os.path.exists(new_media_path):
                raise ValueError(f"A file named '{new_name}{ext}' already exists.")
            os.rename(media_path, new_media_path)

        if twin_path and twin_new_path:
            os.rename(twin_path, twin_new_path)

        old_thumb = thumbnail_path_for(filename)
        if os.path.exists(old_thumb):
            try:
                os.rename(old_thumb, thumbnail_path_for(new_name))
            except OSError:
                pass

        job = self.jobs.pop(filename, None)
        if job:
            job["filename"] = new_name
            self.jobs[new_name] = job

        if filename in self.saved_queue:
            entry = self.saved_queue.pop(filename)
            self.saved_queue[new_name] = entry
            save_queue_to_disk(self.saved_queue)

        job_url = (job or {}).get("url", "") or self.saved_queue.get(new_name, {}).get("url", "")
        write_to_history_log(new_name, job_url, f"RENAMED from {filename}")

        return new_name

    # ── Extracting audio from an already-downloaded video ─────────
    async def extract_audio(self, filename: str) -> dict:
        """Runs ffmpeg to pull the audio track out of an existing video
        into its own file, and adds it to the ledger as a brand new,
        clearly-audio job. Raises ValueError/RuntimeError on failure."""
        source_job = self.jobs.get(filename)
        media_path = find_media_file(filename)
        if not media_path:
            raise ValueError(f"Couldn't find the video file for '{filename}'.")
        if is_audio_file(media_path):
            raise ValueError("That item is already audio-only.")

        save_dir = get_save_dir()
        new_stem = clean_filename(f"{filename} (Audio)")
        if new_stem in self.jobs:
            raise ValueError(f"'{new_stem}' already exists in the ledger.")

        out_path = os.path.join(save_dir, new_stem + ".mp3")
        if os.path.exists(out_path):
            raise ValueError(f"A file named '{new_stem}.mp3' already exists.")

        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y",
                "-i", media_path,
                "-vn",
                "-acodec", "libmp3lame",
                "-q:a", "2",
                out_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                **NO_CONSOLE_KWARGS,
            )
            returncode = await asyncio.wait_for(proc.wait(), timeout=300)
        except FileNotFoundError:
            raise RuntimeError("ffmpeg isn't installed/available on PATH.")
        except asyncio.TimeoutError:
            raise RuntimeError("Audio extraction timed out.")

        if returncode != 0 or not os.path.exists(out_path):
            raise RuntimeError("ffmpeg failed to extract audio from that file.")

        size_bytes = None
        try:
            size_bytes = os.path.getsize(out_path)
        except OSError:
            pass

        job = {
            "filename": new_stem,
            "url": source_job["url"] if source_job else "",
            "res_cap": "Audio Only",
            "status": "DONE",
            "file_size": format_file_size(size_bytes) if size_bytes is not None else "",
            "is_audio": True,
            "playback_position": 0,
            "fully_played": False,
            "pct": 100,
            "total": "",
            "speed": "",
            "eta": "",
        }
        self.jobs[new_stem] = job
        self.saved_queue[new_stem] = {
            "url": job["url"],
            "res_cap": "Audio Only",
            "status": "DONE",
            "is_audio": True,
            "file_size": job["file_size"],
        }
        save_queue_to_disk(self.saved_queue)
        write_to_history_log(new_stem, job["url"], "DONE")

        return job

    # ── Replacing a completed download with its Converted/ twin ──
    async def replace_with_twin(self, filename: str) -> None:
        """Replace the completed download with its twin from Converted/.

        The twin is moved into the configured download folder and replaces
        the original media file. The twin's filename/extension is retained,
        so the ledger continues to identify the item by its filename stem.
        """
        job = self.jobs.get(filename)
        if not job or job.get("status") != "DONE":
            raise ValueError(f"'{filename}' isn't a completed item.")

        media_path = find_media_file(filename)
        if not media_path:
            raise ValueError(f"Couldn't find the original file for '{filename}'.")

        converted_path = find_converted_file(filename)
        if not converted_path:
            raise ValueError(f"Couldn't find a twin for '{filename}' in Converted/.")

        save_dir = get_save_dir()
        if not save_dir:
            raise ValueError("No download folder configured yet.")

        destination = os.path.join(save_dir, os.path.basename(converted_path))

        try:
            # Remove the original first so the twin can take its place even
            # when the filesystem does not allow shutil.move() to overwrite.
            if os.path.normcase(os.path.abspath(destination)) != os.path.normcase(os.path.abspath(media_path)):
                if os.path.exists(destination):
                    os.remove(destination)
                os.remove(media_path)
            else:
                # Defensive fallback for the unlikely case both paths match.
                os.remove(media_path)

            shutil.move(converted_path, destination)
        except OSError as e:
            # If the twin was not moved and the original was removed, report
            # the real filesystem failure rather than silently losing state.
            raise ValueError(f"Replace failed: {e}")

        # Refresh the ledger's media metadata from the newly installed file.
        try:
            probed = await probe_basic_info(destination)
        except Exception:
            probed = {}

        try:
            file_size = os.path.getsize(destination)
        except OSError:
            file_size = None

        job["file_size"] = format_file_size(file_size) if file_size is not None else job.get("file_size", "")
        job["ext"] = os.path.splitext(destination)[1].lstrip(".")
        job["width"] = probed.get("width", job.get("width", 0))
        job["height"] = probed.get("height", job.get("height", 0))
        job["duration"] = probed.get("duration", job.get("duration", 0))
        job["video_codec"] = probed.get("video_codec", job.get("video_codec", ""))
        job["audio_codec"] = probed.get("audio_codec", job.get("audio_codec", ""))
        job["has_twin"] = False
        job["twin_size"] = 0
        job["twin_size_label"] = ""
        job["twin_width"] = 0
        job["twin_height"] = 0

        if filename in self.saved_queue:
            self.saved_queue[filename]["file_size"] = job["file_size"]
            self.saved_queue[filename]["ext"] = job["ext"]
            self.saved_queue[filename]["width"] = job["width"]
            self.saved_queue[filename]["height"] = job["height"]
            self.saved_queue[filename]["duration"] = job["duration"]
            self.saved_queue[filename]["video_codec"] = job["video_codec"]
            self.saved_queue[filename]["audio_codec"] = job["audio_codec"]
            save_queue_to_disk(self.saved_queue)

        await self.connections.broadcast({
            "type": "refresh",
            "jobs": self.snapshot(),
        })

    # ── Moving completed files to the target folder ──────────────
    async def move_to_target(self, filename: str, variant: Optional[str] = None) -> None:
        """Moves a completed file from the current download folder to
        the configured target folder, cleaning up its (now-orphaned)
        thumbnail, and drops it from this folder's ledger/queue.json.

        If a re-encoded twin of this file exists in Converted/ and
        `variant` wasn't given, raises NeedsDecisionError instead of
        moving anything, so the caller can ask the user which version
        they want. Pass variant="original" or variant="reencoded" once
        that decision is made - either way, BOTH the original and the
        re-encoded copy are removed from the download folder afterward,
        since only one of them is meant to survive.

        Raises ValueError on anything else that stops the move."""
        target_dir = get_target_dir()
        if not target_dir:
            raise ValueError("No target folder configured yet.")

        job = self.jobs.get(filename)
        if not job or job.get("status") != "DONE":
            raise ValueError(f"'{filename}' isn't a completed item.")
        if job.get("source_type") == "stash":
            raise ValueError(
                f"'{filename}' was imported from Stash and can't be moved to the target "
                "folder - use Replace Stash Source instead."
            )

        media_path = find_media_file(filename)
        if not media_path:
            raise ValueError(f"Couldn't find the file for '{filename}'.")

        converted_path = find_converted_file(filename)

        if converted_path and variant is None:
            original_info = await probe_basic_info(media_path)
            reencoded_info = await probe_basic_info(converted_path)
            raise NeedsDecisionError({
                "filename": filename,
                "kind": "synchronized" if job.get("synchronized") else "reencoded",
                "original": {
                    "size_bytes": os.path.getsize(media_path),
                    "size_label": format_file_size(os.path.getsize(media_path)),
                    "width": original_info["width"],
                    "height": original_info["height"],
                },
                "reencoded": {
                    "size_bytes": os.path.getsize(converted_path),
                    "size_label": format_file_size(os.path.getsize(converted_path)),
                    "width": reencoded_info["width"],
                    "height": reencoded_info["height"],
                },
            })

        source_path = media_path
        if converted_path and variant == "reencoded":
            source_path = converted_path

        ext = os.path.splitext(source_path)[1]
        dest_path = os.path.join(target_dir, filename + ext)
        if os.path.exists(dest_path):
            raise ValueError(f"'{filename}{ext}' already exists in the target folder.")

        try:
            shutil.move(source_path, dest_path)
        except OSError as e:
            raise ValueError(f"Move failed: {e}")

        # Whichever version didn't get transferred (or, if there was no
        # twin at all, nothing) is cleaned up here.
        leftover_path = converted_path if source_path == media_path else media_path
        if converted_path and leftover_path and os.path.exists(leftover_path):
            try:
                os.remove(leftover_path)
            except OSError:
                pass

        thumb_path = thumbnail_path_for(filename)
        if os.path.exists(thumb_path):
            try:
                os.remove(thumb_path)
            except OSError:
                pass

        self.jobs.pop(filename, None)
        if filename in self.saved_queue:
            del self.saved_queue[filename]
            save_queue_to_disk(self.saved_queue)

    async def move_all_to_target(self) -> dict:
        """Moves every completed (DONE) item to the target folder.
        Returns {'moved': [filenames], 'failed': [{'filename','error'}],
        'pending_decisions': [{'filename','original','reencoded'}]}.
        Items with a re-encoded twin are left untouched in the ledger
        and reported under pending_decisions rather than moved, so the
        caller can prompt for each one and re-call move_to_target with
        an explicit variant. Raises ValueError only if no target folder
        is configured at all - per-file failures are collected instead
        of aborting the batch.

        Stash-imported items are silently skipped rather than reported as
        failures, since they're never eligible for this move (see
        move_to_target) - that's expected and permanent, not an error."""
        if not get_target_dir():
            raise ValueError("No target folder configured yet.")

        candidates = [
            f for f, j in self.jobs.items()
            if j.get("status") == "DONE" and j.get("source_type") != "stash"
        ]
        moved = []
        failed = []
        pending_decisions = []
        for filename in candidates:
            try:
                await self.move_to_target(filename)
                moved.append(filename)
            except NeedsDecisionError as e:
                pending_decisions.append(e.info)
            except ValueError as e:
                failed.append({"filename": filename, "error": str(e)})
        return {"moved": moved, "failed": failed, "pending_decisions": pending_decisions}

    async def move_selected_to_target(self, filenames: list) -> dict:
        """Same as move_all_to_target, but restricted to a caller-supplied
        list of filenames (multi-select in the ledger UI). Filenames that
        aren't completed items, or that fail to move for any other
        reason, land in 'failed'; anything with a re-encoded twin lands
        in 'pending_decisions' just like move_all_to_target, since the
        UI needs to prompt for those regardless of which button
        triggered the move."""
        if not get_target_dir():
            raise ValueError("No target folder configured yet.")

        moved = []
        failed = []
        pending_decisions = []
        for filename in filenames:
            try:
                await self.move_to_target(filename)
                moved.append(filename)
            except NeedsDecisionError as e:
                pending_decisions.append(e.info)
            except ValueError as e:
                failed.append({"filename": filename, "error": str(e)})
        return {"moved": moved, "failed": failed, "pending_decisions": pending_decisions}

    def delete_jobs(self, filenames: list) -> dict:
        """Batch version of delete_job() for multi-select in the ledger
        UI. Skips (rather than deletes) anything currently DOWNLOADING,
        since deleting an in-progress download out from under its running
        task would corrupt that job's state - same guard the single-item
        options menu already applies by hiding Delete while downloading.
        Returns {'deleted': [filenames], 'skipped': [filenames]}."""
        deleted = []
        skipped = []
        for filename in filenames:
            job = self.jobs.get(filename)
            if job and job.get("status") in ("DOWNLOADING", "QUEUED"):
                skipped.append(filename)
                continue
            self.delete_job(filename)
            deleted.append(filename)
        return {"deleted": deleted, "skipped": skipped}
