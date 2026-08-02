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
from typing import Dict, Optional

from config import RES_FORMATS, AUDIO_ONLY_KEY
from ffmpeg_encode import probe_basic_info
from procflags import NO_CONSOLE_KWARGS
from settings import get_save_dir, get_target_dir
from storage import load_saved_queue, save_queue_to_disk, write_to_history_log
from thumbnails import thumbnail_path_for
from ytdlp_utils import (
    clean_filename,
    format_file_size,
    get_downloaded_file_size,
    find_media_file,
    is_audio_file,
)

PROGRESS_RE = re.compile(
    r"\[download\]\s+([\d.]+)%\s+of\s+~?\s*([\d.]+)\s*(\w+)\s+at\s+([\d.]+)\s*([\w/]+)\s+ETA\s+(\d+):(\d+)"
)


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
        With replace=True (used when the download folder changes), any
        previously-tracked completed jobs from the old folder are
        dropped first - active DOWNLOADING jobs are kept regardless,
        since they're mid-flight independent of which folder is
        "current" right now.

        Media metadata (resolution/duration/filetype) is cached in
        queue.json once probed, so this only pays the ffprobe cost for
        files that haven't been seen before (new drops, or the first
        scan after upgrading from a version that didn't track this)."""
        if replace:
            self.jobs = {
                filename: job
                for filename, job in self.jobs.items()
                if job["status"] == "DOWNLOADING"
            }

        disk_queue = load_saved_queue()
        queue_dirty = False

        for job in done_jobs:
            filename = job["filename"]
            width = job.get("width", 0)
            height = job.get("height", 0)
            duration = job.get("duration", 0)
            ext = job.get("ext", "")

            if not ext:
                media_path = find_media_file(filename)
                if media_path:
                    ext = os.path.splitext(media_path)[1].lstrip(".").upper()
                    try:
                        probed = await probe_basic_info(media_path)
                    except Exception:
                        probed = {"width": 0, "height": 0, "duration": 0.0}
                    width, height, duration = probed["width"], probed["height"], probed["duration"]
                    if filename in disk_queue:
                        disk_queue[filename].update({
                            "width": width, "height": height,
                            "duration": duration, "ext": ext,
                        })
                        queue_dirty = True

            self.jobs[filename] = {
                "filename": filename,
                "url": job["url"],
                "res_cap": job["res_cap"],
                "status": job["status"],
                "file_size": job.get("file_size", ""),
                "is_audio": job.get("is_audio", False),
                "playback_position": job.get("playback_position", 0),
                "width": width,
                "height": height,
                "duration": duration,
                "ext": ext,
                "pct": 100,
                "total": "",
                "speed": "",
                "eta": "",
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
            "width": 0,
            "height": 0,
            "duration": 0,
            "ext": "",
            "pct": 0,
            "total": "",
            "speed": "",
            "eta": "",
        }
        self.jobs[filename] = job

        self.saved_queue[filename] = {
            "url": url,
            "res_cap": res_cap,
            "status": "DOWNLOADING",
            "original_input_url": original_pasted_url,
        }
        save_queue_to_disk(self.saved_queue)

        await self.connections.broadcast({"type": "job_added", "job": job})

        asyncio.create_task(self._run_download(job, original_pasted_url))

    async def _run_download(self, job: dict, log_url: str):
        filename = job["filename"]
        out_path = f"{get_save_dir()}/{filename}.%(ext)s"
        is_audio_only = job["res_cap"] == AUDIO_ONLY_KEY

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
                    "--write-thumbnail",
                    "--convert-thumbnails", "jpg",
                    "-o", out_path,
                    job["url"],
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
                    "--write-thumbnail",
                    "--convert-thumbnails", "jpg",
                    "-o", out_path,
                    job["url"],
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    **NO_CONSOLE_KWARGS,
                )
        except Exception:
            # yt-dlp missing/unrunnable — finalize as ERROR instead of
            # leaving the job stuck in DOWNLOADING forever.
            await self._finalize_job(job, "ERROR", log_url)
            return

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
            status = "CANCELLED"
            self._cancelled.discard(filename)
        elif returncode == 0:
            status = "DONE"
        else:
            status = "ERROR"

        await self._finalize_job(job, status, log_url)

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
        if status == "DONE":
            size_bytes = get_downloaded_file_size(filename)
            if size_bytes is not None:
                file_size_str = format_file_size(size_bytes)
            media_path = find_media_file(filename)
            if media_path:
                is_audio = is_audio_file(media_path)
                ext = os.path.splitext(media_path)[1].lstrip(".").upper()
                try:
                    probed = await probe_basic_info(media_path)
                    width, height, duration = probed["width"], probed["height"], probed["duration"]
                except Exception:
                    pass
            self._relocate_downloaded_thumbnail(filename)

        job["status"] = status
        job["file_size"] = file_size_str
        job["is_audio"] = is_audio
        job["width"] = width
        job["height"] = height
        job["duration"] = duration
        job["ext"] = ext

        if filename in self.saved_queue:
            self.saved_queue[filename]["status"] = status
            if file_size_str:
                self.saved_queue[filename]["file_size"] = file_size_str
            self.saved_queue[filename]["is_audio"] = is_audio
            self.saved_queue[filename]["width"] = width
            self.saved_queue[filename]["height"] = height
            self.saved_queue[filename]["duration"] = duration
            self.saved_queue[filename]["ext"] = ext
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
        })

    def _relocate_downloaded_thumbnail(self, filename: str) -> None:
        """yt-dlp writes its thumbnail next to the video by default; move
        it into the isolated stash_dlp_data/.thumbnails folder instead,
        named plainly as <filename>.jpg."""
        source = os.path.join(get_save_dir(), filename + ".jpg")
        if not os.path.exists(source):
            return
        dest = thumbnail_path_for(filename)
        try:
            if os.path.exists(dest):
                os.remove(dest)
            os.rename(source, dest)
        except OSError:
            pass

    # ── Playback position tracking ────────────────────────────────
    def set_playback_position(self, filename: str, position) -> bool:
        """Persists how far into a file playback got, so it can resume
        there next time - survives the browser closing/crashing or the
        server restarting, since it's written straight to queue.json."""
        job = self.jobs.get(filename)
        if not job:
            return False
        try:
            position = max(0.0, float(position))
        except (TypeError, ValueError):
            return False

        job["playback_position"] = position
        if filename in self.saved_queue:
            self.saved_queue[filename]["playback_position"] = position
            save_queue_to_disk(self.saved_queue)
        return True

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
    def cancel_job(self, filename: str) -> bool:
        proc = self._processes.get(filename)
        if proc is None:
            return False
        self._cancelled.add(filename)
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return True

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
        new_name = clean_filename(new_name_raw)
        if not new_name:
            raise ValueError("New name can't be empty.")
        if new_name == filename:
            return filename
        if new_name in self.jobs:
            raise ValueError(f"'{new_name}' is already used by another item.")

        media_path = find_media_file(filename)
        save_dir = get_save_dir()
        if media_path:
            ext = os.path.splitext(media_path)[1]
            new_media_path = os.path.join(save_dir, new_name + ext)
            if os.path.exists(new_media_path):
                raise ValueError(f"A file named '{new_name}{ext}' already exists.")
            os.rename(media_path, new_media_path)

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

    # ── Moving completed files to the target folder ──────────────
    def move_to_target(self, filename: str) -> None:
        """Moves a completed file from the current download folder to
        the configured target folder, cleaning up its (now-orphaned)
        thumbnail, and drops it from this folder's ledger/queue.json.
        Raises ValueError on anything that stops the move."""
        target_dir = get_target_dir()
        if not target_dir:
            raise ValueError("No target folder configured yet.")

        job = self.jobs.get(filename)
        if not job or job.get("status") != "DONE":
            raise ValueError(f"'{filename}' isn't a completed item.")

        media_path = find_media_file(filename)
        if not media_path:
            raise ValueError(f"Couldn't find the file for '{filename}'.")

        ext = os.path.splitext(media_path)[1]
        dest_path = os.path.join(target_dir, filename + ext)
        if os.path.exists(dest_path):
            raise ValueError(f"'{filename}{ext}' already exists in the target folder.")

        try:
            shutil.move(media_path, dest_path)
        except OSError as e:
            raise ValueError(f"Move failed: {e}")

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

    def move_all_to_target(self) -> dict:
        """Moves every completed (DONE) item to the target folder.
        Returns {'moved': [filenames], 'failed': [{'filename','error'}]}.
        Raises ValueError only if no target folder is configured at all -
        per-file failures are collected instead of aborting the batch."""
        if not get_target_dir():
            raise ValueError("No target folder configured yet.")

        candidates = [f for f, j in self.jobs.items() if j.get("status") == "DONE"]
        moved = []
        failed = []
        for filename in candidates:
            try:
                self.move_to_target(filename)
                moved.append(filename)
            except ValueError as e:
                failed.append({"filename": filename, "error": str(e)})
        return {"moved": moved, "failed": failed}
