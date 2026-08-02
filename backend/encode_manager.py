"""In-memory (with on-disk persistence) encode job queue. One ffmpeg
encode runs at a time - see EncodeManager._worker_loop - reading its
-progress pipe:1 output the same way job_manager reads yt-dlp's stdout,
and broadcasting progress/completion over the SAME websocket connections
used for downloads (see main.py, where both managers share one
ConnectionManager) so a single client-side socket stays in sync with
both the download ledger and the encode queue.
"""
import asyncio
import json
import os
import tempfile
import time
import uuid
from typing import Dict, Optional

from config import ENCODE_CODECS, RESOLUTION_CAPS, ASPECT_RATIOS, AUDIO_MODES
from ffmpeg_encode import (
    probe_media, detect_crop, detect_available_hw_encoders,
    estimate_heuristic_bytes, build_video_filters, build_ffmpeg_cmd,
    build_audio_sync_cmd,
)
from procflags import NO_CONSOLE_KWARGS
from settings import get_converted_dir, get_encode_queue_json_path
from ytdlp_utils import format_file_size, clean_filename

PROGRESS_THROTTLE_SEC = 0.75


def _load_saved_encode_queue() -> dict:
    path = get_encode_queue_json_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_encode_queue(data: dict) -> None:
    try:
        with open(get_encode_queue_json_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception:
        pass


class EncodeManager:
    def __init__(self, connections):
        self.jobs: Dict[str, dict] = {}          # job id -> job dict, insertion order preserved
        self._pending: list = []                  # job ids waiting to start, in run order
        self._current_id: Optional[str] = None
        self._process: Optional[asyncio.subprocess.Process] = None
        self._cancelled: set = set()
        self.connections = connections
        self._work_event = asyncio.Event()
        self._worker_task: Optional[asyncio.Task] = None
        self.last_preview_path: Optional[str] = None  # most recent Audio Sync preview clip

    # ── Bootstrapping ────────────────────────────────────────────
    def start(self):
        """Loads any persisted queue and kicks off the serial worker.
        Called once at app startup (see main.py)."""
        saved = _load_saved_encode_queue()
        for job_id, job in saved.items():
            # An ENCODING job at save-time means the app was closed or
            # crashed mid-encode - ffmpeg can't be resumed from a partial
            # output, so surface that honestly instead of pretending it's
            # still running.
            if job.get("status") == "ENCODING":
                job["status"] = "ERROR"
                job["error_message"] = "Interrupted by an app restart."
                job["pct"] = 0
            self.jobs[job_id] = job
            if job.get("status") == "QUEUED":
                self._pending.append(job_id)

        self._worker_task = asyncio.create_task(self._worker_loop())
        if self._pending:
            self._work_event.set()

    def snapshot(self) -> list:
        """Currently-encoding job first (if any), then pending jobs in
        their real run order (so move_up's reordering is actually
        visible), then finished/errored/cancelled jobs newest-first."""
        ordered_ids = []
        if self._current_id:
            ordered_ids.append(self._current_id)
        ordered_ids.extend(self._pending)

        finished_ids = [jid for jid in self.jobs if jid not in ordered_ids]
        finished_ids.reverse()
        ordered_ids.extend(finished_ids)

        return [_public_job(self.jobs[jid]) for jid in ordered_ids if jid in self.jobs]

    def _persist(self):
        _save_encode_queue(self.jobs)

    async def _broadcast(self, message: dict):
        if "job" in message:
            message = {**message, "job": _public_job(message["job"])}
        await self.connections.broadcast(message)

    # ── Capabilities (for the "New Encode Job" form) ──────────────
    async def get_capabilities(self) -> dict:
        hw_available = await detect_available_hw_encoders()
        codecs = {}
        for key, codec_def in ENCODE_CODECS.items():
            available_backends = ["software"]
            for backend, encoder_name in codec_def["hw_encoders"].items():
                if encoder_name in hw_available:
                    available_backends.append(backend)
            codecs[key] = {
                "label": codec_def["label"],
                "preset_kind": codec_def["preset_kind"],
                "default_preset": codec_def["default_preset"],
                "default_crf": codec_def["default_crf"],
                "crf_range": codec_def["crf_range"],
                "container_default": codec_def["container_default"],
                "available_backends": available_backends,
            }
        return {"codecs": codecs, "resolution_caps": list(RESOLUTION_CAPS.keys()),
                "aspect_ratios": list(ASPECT_RATIOS.keys())}

    # ── Probing a candidate source (populates the modal) ──────────
    async def probe_source(self, path: str) -> dict:
        if not os.path.isfile(path):
            raise ValueError("That file doesn't exist.")
        info = await probe_media(path)
        info["size_bytes"] = os.path.getsize(path)
        info["size_label"] = format_file_size(info["size_bytes"])
        return info

    # ── Heuristic pre-encode estimate (live-updates the modal) ─────
    def estimate(self, *, source_info: dict, options: dict) -> int:
        out_w, out_h = _resolve_output_dims(source_info, options)
        return estimate_heuristic_bytes(
            codec=options["codec"],
            crf=int(options.get("crf", ENCODE_CODECS[options["codec"]]["default_crf"])),
            duration=source_info.get("duration", 0),
            out_width=out_w,
            out_height=out_h,
            fps=source_info.get("fps", 30),
            denoise=bool(options.get("denoise")),
            audio_mode=options.get("audio_mode", "copy"),
        )

    # ── Enqueueing a new job ────────────────────────────────────
    async def enqueue(self, source_path: str, options: dict) -> dict:
        if not os.path.isfile(source_path):
            raise ValueError("That source file doesn't exist.")

        source_info = await probe_media(source_path)
        source_size = os.path.getsize(source_path)

        codec = options.get("codec", "h265")
        if codec not in ENCODE_CODECS:
            raise ValueError(f"Unknown codec '{codec}'.")
        codec_def = ENCODE_CODECS[codec]

        out_w, out_h = _resolve_output_dims(source_info, options)
        force_ar = bool(options.get("force_ar"))

        job_id = uuid.uuid4().hex
        stem = clean_filename(os.path.splitext(os.path.basename(source_path))[0])
        container = options.get("container") or codec_def["container_default"]
        output_filename = f"{stem}.{container}"
        reserved = {
            j["output_path"] for j in self.jobs.values()
            if j.get("status") in ("QUEUED", "ENCODING")
        }
        output_path = _unique_output_path(get_converted_dir(), output_filename, reserved)
        output_filename = os.path.basename(output_path)

        mode = options.get("mode", "crf")
        estimated_bytes = 0
        estimate_kind = "heuristic"
        if mode == "crf":
            estimated_bytes = self.estimate(source_info=source_info, options=options)
        else:
            estimated_bytes = int(float(options.get("target_size_mb") or 0) * 1024 * 1024)
            estimate_kind = "target"

        job = {
            "id": job_id,
            "source_path": source_path,
            "source_filename": os.path.basename(source_path),
            "source_size": source_size,
            "source_size_label": format_file_size(source_size),
            "source_width": source_info["width"],
            "source_height": source_info["height"],
            "source_duration": source_info["duration"],
            "source_fps": source_info["fps"],
            "has_audio": source_info["has_audio"],
            "status": "QUEUED",
            "pct": 0,
            "mode": mode,
            "codec": codec,
            "codec_label": codec_def["label"],
            "encoder_backend": options.get("encoder_backend", "software"),
            "crf": int(options.get("crf", codec_def["default_crf"])),
            "preset": options.get("preset", codec_def["default_preset"]),
            "target_size_mb": options.get("target_size_mb"),
            "resolution_cap": options.get("resolution_cap", "source"),
            "output_width": out_w,
            "output_height": out_h,
            "force_ar": force_ar,
            "force_ar_label": options.get("force_ar_label", ""),
            "force_ar_width": options.get("force_ar_width"),
            "force_ar_height": options.get("force_ar_height"),
            "deinterlace": bool(options.get("deinterlace")),
            "auto_crop": bool(options.get("auto_crop")),
            "crop_value": None,
            "denoise": bool(options.get("denoise")),
            "audio_mode": options.get("audio_mode", "copy"),
            "subtitles_mode": options.get("subtitles_mode", "copy"),
            "container": container,
            "oversized_behavior": options.get("oversized_behavior", "flag"),
            "output_path": output_path,
            "output_filename": output_filename,
            "estimated_bytes": estimated_bytes,
            "estimated_size_label": format_file_size(estimated_bytes) if estimated_bytes else "",
            "estimate_kind": estimate_kind,
            "final_bytes": None,
            "final_size_label": "",
            "oversized": False,
            "speed": "",
            "eta_seconds": None,
            "elapsed_seconds": 0,
            "error_message": "",
            "created_at": time.time(),
        }

        self.jobs[job_id] = job
        self._pending.append(job_id)
        self._persist()
        self._work_event.set()

        await self._broadcast({"type": "encode_job_added", "job": job})
        return _public_job(job)

    # ── Audio Sync: enqueueing ──────────────────────────────────
    async def enqueue_audio_sync(self, source_path: str, delay_ms: int) -> dict:
        """Queues a pure-remux audio-delay job - same source and output
        container, only the audio track's timing changes. Shares the
        same job dict shape as a normal encode job (see enqueue() above)
        so the rest of EncodeManager and the frontend card renderer
        don't need to special-case missing keys; encode-specific fields
        just get neutral placeholder values."""
        if not os.path.isfile(source_path):
            raise ValueError("That source file doesn't exist.")
        try:
            delay_ms = int(delay_ms)
        except (TypeError, ValueError):
            raise ValueError("Invalid delay value.")

        source_info = await probe_media(source_path)
        if not source_info["has_audio"]:
            raise ValueError("That file has no audio track to sync.")
        source_size = os.path.getsize(source_path)
        source_size_label = format_file_size(source_size)

        job_id = uuid.uuid4().hex
        # Original filename/extension, unchanged - a straight remux stays
        # in the same container the source was already in.
        output_filename = os.path.basename(source_path)
        container = os.path.splitext(source_path)[1].lstrip(".").lower()
        reserved = {
            j["output_path"] for j in self.jobs.values()
            if j.get("status") in ("QUEUED", "ENCODING")
        }
        output_path = _unique_output_path(get_converted_dir(), output_filename, reserved)
        output_filename = os.path.basename(output_path)

        job = {
            "id": job_id,
            "job_type": "audio_sync",
            "source_path": source_path,
            "source_filename": os.path.basename(source_path),
            "source_size": source_size,
            "source_size_label": source_size_label,
            "source_width": source_info["width"],
            "source_height": source_info["height"],
            "source_duration": source_info["duration"],
            "source_fps": source_info["fps"],
            "has_audio": source_info["has_audio"],
            "status": "QUEUED",
            "pct": 0,
            "mode": "audio_sync",
            "codec": None,
            "codec_label": "Audio Sync",
            "encoder_backend": "software",
            "crf": None,
            "preset": None,
            "target_size_mb": None,
            "resolution_cap": "source",
            "output_width": source_info["width"],
            "output_height": source_info["height"],
            "force_ar": False,
            "force_ar_label": "",
            "force_ar_width": None,
            "force_ar_height": None,
            "deinterlace": False,
            "auto_crop": False,
            "crop_value": None,
            "denoise": False,
            "audio_mode": "copy",
            "subtitles_mode": "copy",
            "container": container,
            "oversized_behavior": "flag",
            "output_path": output_path,
            "output_filename": output_filename,
            "delay_ms": delay_ms,
            "estimated_bytes": source_size,          # a remux barely changes size
            "estimated_size_label": source_size_label,
            "estimate_kind": "heuristic",
            "final_bytes": None,
            "final_size_label": "",
            "oversized": False,
            "speed": "",
            "eta_seconds": None,
            "elapsed_seconds": 0,
            "error_message": "",
            "created_at": time.time(),
        }

        self.jobs[job_id] = job
        self._pending.append(job_id)
        self._persist()
        self._work_event.set()

        await self._broadcast({"type": "encode_job_added", "job": job})
        return _public_job(job)

    # ── Audio Sync: live "Preview Final" render ─────────────────
    async def render_audio_sync_preview(
        self, source_path: str, delay_ms: int, start_seconds: float, preview_len: float = 10.0,
    ) -> str:
        """Renders a short (~preview_len second) clip with the requested
        delay actually applied via the same remux ffmpeg would use for
        the real job - so what the user hears/sees here is exactly what
        the final output will do, not an approximation. Overwrites a
        single reusable temp file each call (this is a single-user local
        app; no need to accumulate preview files)."""
        if not os.path.isfile(source_path):
            raise ValueError("That file doesn't exist.")
        try:
            delay_ms = int(delay_ms)
            start_seconds = max(0.0, float(start_seconds))
        except (TypeError, ValueError):
            raise ValueError("Invalid preview parameters.")

        ext = os.path.splitext(source_path)[1] or ".mp4"
        preview_path = os.path.join(tempfile.gettempdir(), f"stash_dlp_audio_sync_preview{ext}")

        cmd = build_audio_sync_cmd(
            source_path=source_path, output_path=preview_path, delay_ms=delay_ms,
            start_seconds=start_seconds, duration=preview_len,
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                **NO_CONSOLE_KWARGS,
            )
        except FileNotFoundError:
            raise RuntimeError("ffmpeg isn't installed/available on PATH.")

        try:
            _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise RuntimeError("Rendering the preview timed out.")

        if proc.returncode != 0 or not os.path.exists(preview_path):
            tail_lines = stderr_bytes.decode("utf-8", errors="ignore").strip().splitlines()
            tail_text = "\n".join(tail_lines[-12:])
            raise RuntimeError(f"Couldn't render the preview.\n{tail_text}" if tail_text else "Couldn't render the preview.")

        self.last_preview_path = preview_path
        return preview_path

    # ── Serial worker ─────────────────────────────────────────────
    async def _worker_loop(self):
        while True:
            if not self._pending:
                self._work_event.clear()
                await self._work_event.wait()
                continue

            job_id = self._pending.pop(0)
            job = self.jobs.get(job_id)
            if not job or job["status"] != "QUEUED":
                continue

            self._current_id = job_id
            try:
                await self._run_job(job)
            except Exception as e:
                job["status"] = "ERROR"
                job["error_message"] = str(e) or "Encoding failed unexpectedly."
                self._persist()
                await self._broadcast({"type": "encode_job_updated", "job": job})
            finally:
                self._current_id = None
                self._process = None

    async def _run_job(self, job: dict):
        job_id = job["id"]
        job["status"] = "ENCODING"
        job["pct"] = 0
        started = time.time()
        await self._broadcast({"type": "encode_job_updated", "job": job})

        if job.get("job_type") == "audio_sync":
            await self._run_audio_sync(job, started)
            return

        # Auto-crop detection happens once, right before building the
        # filter chain, since it needs to sample the actual source.
        crop_value = None
        if job["auto_crop"]:
            crop_value = await detect_crop(
                job["source_path"], job["source_width"], job["source_height"], job["source_duration"],
            )
            job["crop_value"] = crop_value

        video_filters = build_video_filters(
            source_width=job["source_width"], source_height=job["source_height"],
            deinterlace=job["deinterlace"],
            force_ar=job["force_ar"], force_ar_width=job["force_ar_width"] or 0,
            force_ar_height=job["force_ar_height"] or 0,
            resolution_cap=RESOLUTION_CAPS.get(job["resolution_cap"]),
            crop=crop_value, denoise=job["denoise"],
        )

        os.makedirs(os.path.dirname(job["output_path"]), exist_ok=True)

        if job["mode"] == "size":
            await self._run_two_pass(job, video_filters, started)
        else:
            await self._run_single_pass(job, video_filters, started)

    async def _run_audio_sync(self, job: dict, started: float):
        """Mirrors _run_single_pass below, but with a remux (build_audio_
        sync_cmd) instead of a real encode - same subprocess/progress/
        finalize plumbing, since -progress pipe:1 reports usefully even
        for a stream-copy job."""
        os.makedirs(os.path.dirname(job["output_path"]), exist_ok=True)
        cmd = build_audio_sync_cmd(
            source_path=job["source_path"], output_path=job["output_path"],
            delay_ms=job["delay_ms"],
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **NO_CONSOLE_KWARGS,
            )
        except FileNotFoundError:
            job["status"] = "ERROR"
            job["error_message"] = "ffmpeg isn't installed/available on PATH."
            self._persist()
            await self._broadcast({"type": "encode_job_updated", "job": job})
            return

        self._process = proc
        stderr_tail: list = []
        stderr_task = asyncio.create_task(_drain_stderr(proc, stderr_tail))
        await self._pump_progress(job, proc, started, pct_floor=0, pct_ceiling=100)
        returncode = await proc.wait()
        await stderr_task
        await self._finalize(job, returncode, started, stderr_tail=stderr_tail, cmd=cmd)

    async def _run_single_pass(self, job: dict, video_filters: str, started: float):
        cmd = build_ffmpeg_cmd(
            source_path=job["source_path"], output_path=job["output_path"],
            codec=job["codec"], encoder_backend=job["encoder_backend"],
            crf=job["crf"], preset=job["preset"], video_filters=video_filters,
            audio_mode=job["audio_mode"], subtitles_mode=job["subtitles_mode"],
            container=job["container"],
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **NO_CONSOLE_KWARGS,
            )
        except FileNotFoundError:
            job["status"] = "ERROR"
            job["error_message"] = "ffmpeg isn't installed/available on PATH."
            self._persist()
            await self._broadcast({"type": "encode_job_updated", "job": job})
            return

        self._process = proc
        stderr_tail: list = []
        stderr_task = asyncio.create_task(_drain_stderr(proc, stderr_tail))
        await self._pump_progress(job, proc, started, pct_floor=0, pct_ceiling=100)
        returncode = await proc.wait()
        await stderr_task
        await self._finalize(job, returncode, started, stderr_tail=stderr_tail, cmd=cmd)

    async def _run_two_pass(self, job: dict, video_filters: str, started: float):
        """Target-size mode: compute a bitrate from the requested size and
        run ffmpeg's standard 2-pass sequence. Pass 1 (analysis only, no
        output file) is weighted as the first ~15% of the job's overall
        progress, pass 2 (the real encode) as the remaining ~85% - pass 1
        is normally much quicker since it doesn't mux audio or write a
        real file."""
        target_bytes = int(float(job["target_size_mb"] or 0) * 1024 * 1024)
        duration = job["source_duration"] or 1
        audio_bps = 0
        if job["audio_mode"] == "aac128":
            audio_bps = 128_000
        elif job["audio_mode"] == "aac192":
            audio_bps = 192_000
        elif job["audio_mode"] == "copy" and job["has_audio"]:
            audio_bps = 160_000  # rough guess for an unknown source audio bitrate

        video_bitrate = max(100_000, int((target_bytes * 8) / duration) - audio_bps)

        pass_log = os.path.join(os.path.dirname(job["output_path"]), f".passlog_{job['id']}")

        cmd_pass1 = build_ffmpeg_cmd(
            source_path=job["source_path"], output_path=job["output_path"],
            codec=job["codec"], encoder_backend=job["encoder_backend"],
            crf=job["crf"], preset=job["preset"], video_filters=video_filters,
            audio_mode=job["audio_mode"], subtitles_mode=job["subtitles_mode"],
            container=job["container"], two_pass_bitrate=video_bitrate,
            pass_num=1, pass_log_path=pass_log,
        )
        try:
            proc1 = await asyncio.create_subprocess_exec(
                *cmd_pass1,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **NO_CONSOLE_KWARGS,
            )
        except FileNotFoundError:
            job["status"] = "ERROR"
            job["error_message"] = "ffmpeg isn't installed/available on PATH."
            self._persist()
            await self._broadcast({"type": "encode_job_updated", "job": job})
            return

        self._process = proc1
        stderr_tail: list = []
        stderr_task1 = asyncio.create_task(_drain_stderr(proc1, stderr_tail))
        await self._pump_progress(job, proc1, started, pct_floor=0, pct_ceiling=15)
        rc1 = await proc1.wait()
        await stderr_task1
        if job["id"] in self._cancelled or rc1 != 0:
            await self._finalize(job, rc1 if rc1 != 0 else 1, started, stderr_tail=stderr_tail, cmd=cmd_pass1)
            _cleanup_pass_log(pass_log)
            return

        cmd_pass2 = build_ffmpeg_cmd(
            source_path=job["source_path"], output_path=job["output_path"],
            codec=job["codec"], encoder_backend=job["encoder_backend"],
            crf=job["crf"], preset=job["preset"], video_filters=video_filters,
            audio_mode=job["audio_mode"], subtitles_mode=job["subtitles_mode"],
            container=job["container"], two_pass_bitrate=video_bitrate,
            pass_num=2, pass_log_path=pass_log,
        )
        proc2 = await asyncio.create_subprocess_exec(
            *cmd_pass2,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **NO_CONSOLE_KWARGS,
        )
        self._process = proc2
        stderr_tail2: list = []
        stderr_task2 = asyncio.create_task(_drain_stderr(proc2, stderr_tail2))
        await self._pump_progress(job, proc2, started, pct_floor=15, pct_ceiling=100)
        rc2 = await proc2.wait()
        await stderr_task2
        await self._finalize(job, rc2, started, stderr_tail=stderr_tail2, cmd=cmd_pass2)
        _cleanup_pass_log(pass_log)

    async def _pump_progress(self, job: dict, proc, started: float, pct_floor: int, pct_ceiling: int):
        duration = job["source_duration"] or 0
        last_broadcast = 0.0
        block: dict = {}

        async for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", errors="ignore").strip()
            if not line or "=" not in line:
                continue
            key, _, value = line.partition("=")
            block[key] = value

            if key != "progress":
                continue

            out_time_sec = _parse_out_time(block)
            frac = min(1.0, out_time_sec / duration) if duration > 0 else 0.0
            job["pct"] = int(pct_floor + frac * (pct_ceiling - pct_floor))
            job["speed"] = block.get("speed", "").strip() or job["speed"]
            job["elapsed_seconds"] = round(time.time() - started, 1)

            speed_val = _parse_speed(job["speed"])
            if speed_val and duration > 0:
                remaining = max(0.0, duration - out_time_sec)
                job["eta_seconds"] = round(remaining / speed_val, 1)

            # Live-refine the estimate from actual bytes written so far.
            # Two guards before trusting it: enough content-time fraction
            # has elapsed for the ratio to be meaningful, AND enough real
            # wall-clock time has passed for ffmpeg's muxer to have
            # actually flushed data proportional to that fraction (a very
            # fast encode can buffer most output right up until the end,
            # making bytes-on-disk a poor proxy until real time catches
            # up). Below either threshold, keep showing the heuristic.
            if frac > 0.05 and job["elapsed_seconds"] >= 2 and job.get("mode") == "crf":
                try:
                    bytes_so_far = os.path.getsize(job["output_path"])
                except OSError:
                    bytes_so_far = _parse_int(block.get("total_size"))
                if bytes_so_far:
                    bytes_so_far = max(bytes_so_far, job.get("_max_bytes_seen", 0))
                    job["_max_bytes_seen"] = bytes_so_far
                    live_projection = bytes_so_far / frac
                    weight = min(0.7, (frac - 0.05) / 0.5)  # never fully overrides the running estimate
                    blended = live_projection * weight + job["estimated_bytes"] * (1 - weight)
                    blended = max(blended, bytes_so_far)
                    job["estimated_bytes"] = int(blended)
                    job["estimated_size_label"] = format_file_size(job["estimated_bytes"])
                    job["estimate_kind"] = "live"

            now = time.time()
            if now - last_broadcast >= PROGRESS_THROTTLE_SEC:
                last_broadcast = now
                await self._broadcast({"type": "encode_job_progress", "job": job})

            block = {}
            if job["id"] in self._cancelled:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                break

    async def _finalize(self, job: dict, returncode: int, started: float, stderr_tail=None, cmd=None):
        job["elapsed_seconds"] = round(time.time() - started, 1)
        job_id = job["id"]

        if job_id in self._cancelled:
            self._cancelled.discard(job_id)
            job["status"] = "CANCELLED"
            job["pct"] = 0
            _remove_partial_output(job["output_path"])
        elif returncode == 0 and os.path.exists(job["output_path"]):
            final_bytes = os.path.getsize(job["output_path"])
            job["final_bytes"] = final_bytes
            job["final_size_label"] = format_file_size(final_bytes)
            job["pct"] = 100

            if job.get("job_type") != "audio_sync" and final_bytes >= job["source_size"]:
                job["oversized"] = True
                if job["oversized_behavior"] == "discard":
                    _remove_partial_output(job["output_path"])
                    job["status"] = "ERROR"
                    job["error_message"] = (
                        f"Encoded output ({job['final_size_label']}) was larger than the "
                        f"source ({job['source_size_label']}) - discarded per your settings."
                    )
                else:
                    job["status"] = "DONE"
            else:
                job["status"] = "DONE"
        else:
            job["status"] = "ERROR"
            job["error_message"] = job["error_message"] or _build_error_message(returncode, stderr_tail, cmd)
            _remove_partial_output(job["output_path"])

        self._persist()
        await self._broadcast({"type": "encode_job_updated", "job": job})

    # ── Controls ───────────────────────────────────────────────
    def cancel_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job:
            return False

        if job_id == self._current_id:
            self._cancelled.add(job_id)
            if self._process is not None:
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass
            return True

        if job_id in self._pending:
            self._pending.remove(job_id)
            job["status"] = "CANCELLED"
            job["pct"] = 0
            self._persist()
            asyncio.create_task(self._broadcast({"type": "encode_job_updated", "job": job}))
            return True

        return False

    def move_up(self, job_id: str) -> bool:
        if job_id not in self._pending:
            return False
        idx = self._pending.index(job_id)
        if idx == 0:
            return False
        self._pending[idx - 1], self._pending[idx] = self._pending[idx], self._pending[idx - 1]
        return True

    async def retry_job(self, job_id: str) -> dict:
        job = self.jobs.get(job_id)
        if not job:
            raise ValueError("That job no longer exists.")
        if job["status"] not in ("ERROR", "CANCELLED"):
            raise ValueError("Only errored or cancelled jobs can be retried.")

        job["status"] = "QUEUED"
        job["pct"] = 0
        job["error_message"] = ""
        job["oversized"] = False
        job["final_bytes"] = None
        job["final_size_label"] = ""
        job["estimate_kind"] = "heuristic"
        self._pending.append(job_id)
        self._persist()
        self._work_event.set()
        await self._broadcast({"type": "encode_job_updated", "job": job})
        return _public_job(job)

    def delete_job(self, job_id: str, delete_output: bool = False) -> bool:
        job = self.jobs.get(job_id)
        if not job:
            return False
        if job["status"] == "ENCODING":
            raise ValueError("Cancel this job before removing it from the list.")

        if job_id in self._pending:
            self._pending.remove(job_id)

        if delete_output and job.get("output_path") and os.path.exists(job["output_path"]):
            try:
                os.remove(job["output_path"])
            except OSError:
                pass

        self.jobs.pop(job_id, None)
        self._persist()
        return True


async def _drain_stderr(proc, buffer: list, max_lines: int = 60):
    """Runs concurrently with stdout progress reading - ffmpeg writes
    both streams at once, and only reading one risks the OS pipe buffer
    for the other filling up and deadlocking the whole process. Keeps
    just the last max_lines, since that's normally enough to show the
    actual error (the encoder/filter that failed, a missing DLL, an
    unsupported option) without dragging in the whole startup banner."""
    if proc.stderr is None:
        return
    async for raw_line in proc.stderr:
        line = raw_line.decode("utf-8", errors="ignore").rstrip()
        if not line:
            continue
        buffer.append(line)
        if len(buffer) > max_lines:
            buffer.pop(0)


def _parse_out_time(block: dict) -> float:
    if "out_time_us" in block:
        try:
            return max(0.0, int(block["out_time_us"]) / 1_000_000)
        except ValueError:
            pass
    if "out_time_ms" in block:
        try:
            return max(0.0, int(block["out_time_ms"]) / 1_000_000)
        except ValueError:
            pass
    out_time = block.get("out_time", "")
    if ":" in out_time:
        try:
            h, m, s = out_time.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
        except ValueError:
            pass
    return 0.0


def _parse_speed(speed_str: str):
    try:
        return float(speed_str.rstrip("x"))
    except (ValueError, AttributeError):
        return None


def _parse_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _build_error_message(returncode: int, stderr_tail, cmd) -> str:
    # A normal ffmpeg error exits with a small code (usually 1). Anything
    # far outside 0-255 (Windows reports the raw process exit code, not
    # POSIX-style 0-255) almost always means ffmpeg.exe crashed rather
    # than exited cleanly - most commonly a hardware encoder (AMF/NVENC/
    # QSV) hitting a driver it doesn't like. Flag that distinction rather
    # than just printing the number.
    if returncode > 255 or returncode < -255:
        encoder = ""
        if cmd and "-c:v" in cmd:
            encoder = cmd[cmd.index("-c:v") + 1]
        base = (
            f"ffmpeg.exe appears to have crashed (raw exit code {returncode}, "
            f"0x{returncode & 0xFFFFFFFF:08X}) rather than exiting normally."
        )
        if encoder:
            base += f" This was using the '{encoder}' encoder - if that's a hardware encoder, this usually means the driver rejected it."
    else:
        base = f"ffmpeg exited with code {returncode}."

    if stderr_tail:
        tail_text = "\n".join(stderr_tail[-12:])
        return f"{base}\n{tail_text}"
    return base


def _remove_partial_output(path: str):
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def _cleanup_pass_log(pass_log_prefix: str):
    """ffmpeg's 2-pass logging writes '<prefix>-0.log' (and sometimes
    '<prefix>-0.log.mbtree') next to the prefix we pass via
    -passlogfile; clean those up once both passes are done."""
    for suffix in ("-0.log", "-0.log.mbtree"):
        path = pass_log_prefix + suffix
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


def _public_job(job: dict) -> dict:
    return {k: v for k, v in job.items() if not k.startswith("_")}


def _unique_output_path(directory: str, filename: str, reserved: set) -> str:
    """Appends ' (2)', ' (3)', ... before the extension until neither an
    existing file nor another queued/in-flight job already claims that
    path - avoids two encode jobs from the same source silently
    clobbering each other's output."""
    stem, ext = os.path.splitext(filename)
    candidate = os.path.join(directory, filename)
    counter = 2
    while os.path.exists(candidate) or candidate in reserved:
        candidate = os.path.join(directory, f"{stem} ({counter}){ext}")
        counter += 1
    return candidate


def _resolve_output_dims(source_info: dict, options: dict):
    """Applies force-aspect-ratio correction (if any) then the
    resolution-cap downscale (if any) to get the dimensions the output
    will actually end up at, for estimation and display purposes."""
    width = source_info.get("width", 0)
    height = source_info.get("height", 0)

    if options.get("force_ar") and options.get("force_ar_width") and options.get("force_ar_height"):
        width = int(options["force_ar_width"])
        height = int(options["force_ar_height"])

    cap = RESOLUTION_CAPS.get(options.get("resolution_cap", "source"))
    if cap and height > cap:
        new_height = cap
        new_width = int(round(width * (cap / height) / 2) * 2)  # keep it even, ffmpeg-friendly
        width, height = new_width, new_height

    return width, height
