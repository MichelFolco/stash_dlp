"""ffmpeg/ffprobe plumbing for the Encode Manager: probing source media,
estimating output size before/while encoding, detecting which hardware
encoders are actually usable on this machine, and building the ffmpeg
argv for a given job's settings.

Kept separate from encode_manager.py (which owns job state/lifecycle) so
the actual ffmpeg mechanics can be tested/reasoned about on their own.
"""
import asyncio
import json
import os
import re

from config import ENCODE_CODECS, ASPECT_RATIOS
from procflags import NO_CONSOLE_KWARGS

PROBE_TIMEOUT = 20
CROPDETECT_TIMEOUT = 30


# ── Probing ──────────────────────────────────────────────────────
async def probe_media(path: str) -> dict:
    """Returns {width, height, duration, has_audio, video_codec, fps} for
    a media file, or raises RuntimeError if ffprobe can't read it."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error",
            "-print_format", "json",
            "-show_format", "-show_streams",
            path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            **NO_CONSOLE_KWARGS,
        )
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=PROBE_TIMEOUT)
    except FileNotFoundError:
        raise RuntimeError("ffprobe isn't installed/available on PATH.")
    except asyncio.TimeoutError:
        raise RuntimeError("Probing the source file timed out.")

    try:
        data = json.loads(stdout_bytes.decode("utf-8", errors="ignore"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise RuntimeError("Couldn't read that file - ffprobe returned no usable data.")

    streams = data.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video_stream:
        raise RuntimeError("No video stream found in that file.")

    fmt = data.get("format", {})
    duration = _safe_float(fmt.get("duration")) or _safe_float(video_stream.get("duration")) or 0.0

    fps = 0.0
    rate = video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate") or "0/0"
    if "/" in rate:
        num, _, den = rate.partition("/")
        try:
            num_f, den_f = float(num), float(den)
            fps = num_f / den_f if den_f else 0.0
        except ValueError:
            fps = 0.0

    return {
        "width": int(video_stream.get("width") or 0),
        "height": int(video_stream.get("height") or 0),
        "duration": duration,
        "has_audio": audio_stream is not None,
        "video_codec": video_stream.get("codec_name", ""),
        "fps": round(fps, 2),
    }


async def probe_basic_info(path: str) -> dict:
    """Lightweight probe for download-ledger metadata: returns
    {width, height, duration, video_codec, audio_codec} for ANY media
    file, audio or video - unlike probe_media() above (which is
    Encode-Manager-specific and raises if there's no video stream).
    width/height/video_codec come back 0/"" for audio-only files.
    Best-effort: probing failures return zeros/blanks rather than
    raising, since this is just display data and shouldn't block a
    completed download over a flaky ffprobe call."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error",
            "-print_format", "json",
            "-show_format", "-show_streams",
            path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            **NO_CONSOLE_KWARGS,
        )
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=PROBE_TIMEOUT)
        data = json.loads(stdout_bytes.decode("utf-8", errors="ignore"))
    except Exception:
        return {"width": 0, "height": 0, "duration": 0.0, "video_codec": "", "audio_codec": ""}

    streams = data.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})
    duration = _safe_float(fmt.get("duration")) or 0.0

    width = height = 0
    video_codec = audio_codec = ""
    if video_stream:
        width = int(video_stream.get("width") or 0)
        height = int(video_stream.get("height") or 0)
        video_codec = video_stream.get("codec_name", "") or ""
        if not duration:
            duration = _safe_float(video_stream.get("duration")) or 0.0
    if audio_stream:
        audio_codec = audio_stream.get("codec_name", "") or ""

    return {
        "width": width, "height": height, "duration": duration,
        "video_codec": video_codec, "audio_codec": audio_codec,
    }


def _safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def detect_crop(path: str, width: int, height: int, duration: float):
    """Samples a chunk of the video with ffmpeg's cropdetect filter and
    returns 'W:H:X:Y' (ffmpeg's crop-filter argument string), or None if
    detection fails/finds nothing to crop. Samples from ~10% into the
    file for up to 15s, to skip any black intro/logo bumper that would
    otherwise skew the detected crop."""
    if not width or not height:
        return None

    start = max(0.0, (duration or 0) * 0.1)
    sample_len = min(15, max(3, int((duration or 15) * 0.2)))

    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner",
            "-ss", str(start),
            "-i", path,
            "-t", str(sample_len),
            "-vf", "cropdetect=24:16:0",
            "-f", "null", "-",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            **NO_CONSOLE_KWARGS,
        )
        _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=CROPDETECT_TIMEOUT)
    except Exception:
        return None

    text = stderr_bytes.decode("utf-8", errors="ignore")
    matches = re.findall(r"crop=(\d+:\d+:\d+:\d+)", text)
    if not matches:
        return None
    crop = matches[-1]  # last detected value is the most settled reading
    cw, ch, _, _ = (int(x) for x in crop.split(":"))
    if cw >= width and ch >= height:
        return None  # nothing to crop
    return crop


# ── Hardware encoder detection ───────────────────────────────────
_hw_encoder_cache = None


async def detect_available_hw_encoders() -> set:
    """Returns the set of hardware encoder names (e.g. {'hevc_amf'}) this
    ffmpeg build actually lists, so the UI only offers backends that will
    work rather than fail at encode time. Cached for the process lifetime -
    the installed ffmpeg build doesn't change while the app is running."""
    global _hw_encoder_cache
    if _hw_encoder_cache is not None:
        return _hw_encoder_cache

    known = set()
    for codec_def in ENCODE_CODECS.values():
        known.update(codec_def.get("hw_encoders", {}).values())

    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-encoders",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            **NO_CONSOLE_KWARGS,
        )
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        text = stdout_bytes.decode("utf-8", errors="ignore")
    except Exception:
        _hw_encoder_cache = set()
        return _hw_encoder_cache

    available = {name for name in known if re.search(rf"\b{re.escape(name)}\b", text)}
    _hw_encoder_cache = available
    return available


# ── Size estimation ───────────────────────────────────────────────
# Rough bits-per-pixel-per-second figures for "visually clean" output at
# a mid-range CRF, by codec - AV1/HEVC compress noticeably better than
# H.264 at the same perceived quality, VP9 sits close to HEVC. These are
# ballpark multipliers for the PRE-ENCODE heuristic estimate only; once a
# job is actually running, the live estimate (bytes-written / progress)
# replaces this and is far more accurate.
_BASE_BPP = {
    "h265": 0.06,
    "h264": 0.10,
    "av1": 0.045,
    "vp9": 0.065,
}


def estimate_heuristic_bytes(
    *, codec: str, crf: int, duration: float, out_width: int, out_height: int,
    fps: float, denoise: bool, source_audio_bytes_per_sec: float = 16000,
    audio_mode: str = "copy",
) -> int:
    """A pre-encode ballpark, not a guarantee - see _BASE_BPP docstring
    above. Scales the base bits-per-pixel figure by how far the chosen
    CRF sits from that codec's default (each ~6 CRF steps roughly halves
    or doubles output size, which matches typical x264/x265 behavior)."""
    if duration <= 0 or out_width <= 0 or out_height <= 0:
        return 0

    codec_def = ENCODE_CODECS.get(codec, ENCODE_CODECS["h265"])
    base_bpp = _BASE_BPP.get(codec, 0.06)
    default_crf = codec_def["default_crf"]

    crf_delta = crf - default_crf
    scale = 2 ** (crf_delta / 6.0)
    bpp = base_bpp * scale

    pixels_per_sec = out_width * out_height * max(fps, 24.0)
    video_bps = bpp * pixels_per_sec
    if denoise:
        video_bps *= 0.92  # denoise typically shaves a bit more off compressibility

    if audio_mode == "copy":
        audio_bps = source_audio_bytes_per_sec * 8
    elif audio_mode == "aac128":
        audio_bps = 128_000
    elif audio_mode == "aac192":
        audio_bps = 192_000
    else:
        audio_bps = 0

    total_bps = video_bps + audio_bps
    return int((total_bps * duration) / 8)


# ── ffmpeg command construction ──────────────────────────────────
def _preset_args(codec: str, encoder: str, preset: str) -> list:
    codec_def = ENCODE_CODECS[codec]
    kind = codec_def["preset_kind"]
    if kind == "x26x":
        return ["-preset", preset]
    if kind == "svt":
        return ["-preset", str(preset)]
    if kind == "vpx":
        return ["-deadline", "good", "-cpu-used", str(preset)]
    return []


def _quality_args(codec: str, encoder_backend: str, encoder: str, crf: int) -> list:
    codec_def = ENCODE_CODECS[codec]
    if encoder_backend == "software":
        if codec_def["preset_kind"] == "vpx":
            return ["-b:v", "0", "-crf", str(crf)]
        return ["-crf", str(crf)]

    # Hardware backends: CRF isn't a universal concept, so approximate
    # with a constant-QP mode. This is a reasonable approximation, not a
    # tuned match to libx265's CRF scale - AMF/NVENC/QSV quality-per-QP
    # varies by driver/generation, so treat this as a starting point and
    # adjust the slider by feel.
    if encoder_backend == "amf":
        return ["-rc", "cqp", "-qp_i", str(crf), "-qp_p", str(crf), "-qp_b", str(crf)]
    if encoder_backend == "nvenc":
        return ["-rc", "constqp", "-qp", str(crf)]
    if encoder_backend == "qsv":
        return ["-global_quality", str(crf)]
    return ["-crf", str(crf)]


def build_video_filters(
    *, source_width: int, source_height: int, deinterlace: bool,
    force_ar: bool, force_ar_width: int, force_ar_height: int,
    resolution_cap: int, crop: str, denoise: bool,
) -> str:
    """Builds the -vf filter-chain string. Order matters: deinterlace
    first (operates on the raw field structure), then the aspect-ratio
    fix (non-uniform scale back to correct proportions for sources whose
    pixels are actually stored squished), then crop, then any
    downscale-for-size-reduction cap (now measured against the
    already-corrected dimensions), then denoise last (right before
    encoding, so it isn't undone by any resize after it)."""
    filters = []

    if deinterlace:
        filters.append("bwdif=1")

    working_w, working_h = source_width, source_height
    if force_ar and force_ar_width and force_ar_height:
        filters.append(f"scale={force_ar_width}:{force_ar_height}:flags=lanczos,setsar=1")
        working_w, working_h = force_ar_width, force_ar_height

    if crop:
        filters.append(f"crop={crop}")
        working_h = int(crop.split(":")[1])

    if resolution_cap and working_h > resolution_cap:
        filters.append(f"scale=-2:{resolution_cap}:force_original_aspect_ratio=decrease")

    if denoise:
        filters.append("hqdn3d=2:1.5:3:2.5")

    return ",".join(filters)


def build_audio_args(audio_mode: str) -> list:
    if audio_mode == "copy":
        return ["-c:a", "copy"]
    if audio_mode == "aac128":
        return ["-c:a", "aac", "-b:a", "128k"]
    if audio_mode == "aac192":
        return ["-c:a", "aac", "-b:a", "192k"]
    return ["-an"]


def build_subtitle_args(subtitles_mode: str, container: str) -> list:
    if subtitles_mode == "drop":
        return ["-sn"]
    # "copy": mp4 can't carry most text subtitle codecs directly, so
    # transcode to mov_text there; mkv can just copy as-is.
    if container == "mp4":
        return ["-c:s", "mov_text"]
    return ["-c:s", "copy"]


def build_ffmpeg_cmd(
    *, source_path: str, output_path: str, codec: str, encoder_backend: str,
    crf: int, preset: str, video_filters: str, audio_mode: str,
    subtitles_mode: str, container: str, two_pass_bitrate: int = 0,
    pass_num: int = 0, pass_log_path: str = "",
) -> list:
    """Builds the full ffmpeg argv for one job. If two_pass_bitrate is
    set, builds a 2-pass (target-size) invocation for the given pass_num
    (1 or 2) instead of CRF/quality mode."""
    codec_def = ENCODE_CODECS[codec]
    if encoder_backend == "software":
        encoder = codec_def["software_encoder"]
    else:
        encoder = codec_def["hw_encoders"][encoder_backend]

    cmd = ["ffmpeg", "-y", "-i", source_path]
    if video_filters:
        cmd += ["-vf", video_filters]
    cmd += ["-c:v", encoder]
    cmd += _preset_args(codec, encoder, preset)

    if two_pass_bitrate:
        cmd += ["-b:v", f"{two_pass_bitrate}", "-pass", str(pass_num), "-passlogfile", pass_log_path]
        if pass_num == 1:
            cmd += ["-an", "-f", "null", os.devnull]
            cmd += ["-progress", "pipe:1", "-nostats"]
            return cmd
    else:
        cmd += _quality_args(codec, encoder_backend, encoder, crf)

    cmd += build_audio_args(audio_mode)
    cmd += build_subtitle_args(subtitles_mode, container)
    cmd += ["-progress", "pipe:1", "-nostats", output_path]
    return cmd
