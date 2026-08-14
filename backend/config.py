"""Shared configuration and paths.

The download folder is user-changeable at runtime (see settings.py /
the "Download Folder..." option in the app's logo menu), so it's no
longer a fixed constant here. STASH_DLP_SAVE_DIR still works as the
initial default the first time the app runs, before any folder has
been chosen via the UI.
"""
import os
import sys
from pathlib import Path

# stash_dlp's own version (NOT yt-dlp's - see version_state/api/version in
# main.py for that). Shown to the user via the /api/app_version endpoint
# when they click the logo (see app.js showAppVersion()).
#
# MAINTENANCE: this must always match the top entry of
# CHANGELOG_StashDLP.md. Whenever a change warrants a new changelog
# entry, bump this string in the SAME commit/edit. This applies to any
# LLM/agent editing this codebase as much as a human - do not add a
# CHANGELOG_StashDLP.md entry without also updating APP_VERSION here.
APP_VERSION = "1.00"

BACKEND_DIR = Path(__file__).resolve().parent

FROZEN = getattr(sys, "frozen", False)

if FROZEN:
    # Running as a PyInstaller-bundled exe: __file__-based paths point
    # into the temporary extraction folder (sys._MEIPASS), which is wiped
    # after the process exits. Persist settings/downloads next to the
    # actual exe instead, so they survive between runs.
    PROJECT_ROOT = Path(sys.executable).resolve().parent
    # Bundled read-only assets (static/) DO live under _MEIPASS though -
    # that's genuinely where PyInstaller unpacks --add-data files.
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", str(BACKEND_DIR.parent)))
else:
    PROJECT_ROOT = BACKEND_DIR.parent
    BUNDLE_DIR = PROJECT_ROOT

# Where the app's own bookkeeping lives - this location is fixed
# regardless of which folder downloads currently go to.
SETTINGS_JSON_PATH = os.path.join(str(PROJECT_ROOT), "_app_settings.json")

# Per-download-folder data (queue, encode queue, history log, thumbnails)
# used to live inside each download folder itself (stash_dlp_data/). It
# now lives centrally here instead, one subfolder per download folder
# ever used, keyed by a hash of that folder's path - see
# settings._folder_data_key(). Fixed location, like SETTINGS_JSON_PATH.
LIBRARY_DATA_DIR = os.path.join(str(PROJECT_ROOT), "library_data")

DEFAULT_SAVE_DIR = os.environ.get("STASH_DLP_SAVE_DIR", str(PROJECT_ROOT))

# Defaults to loopback-only. Set STASH_DLP_HOST=0.0.0.0 to also accept
# connections from other devices on your Tailscale network / LAN.
HOST = os.environ.get("STASH_DLP_HOST", "127.0.0.1")
PORT = int(os.environ.get("STASH_DLP_PORT", "8722"))


# yt-dlp output format caps, identical to the desktop app
RES_FORMATS = {
    "480p": "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best",
    "720p": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best",
    "Best": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best",
}

# A distinct res_cap value (not in RES_FORMATS) that job_manager treats as
# a structurally different yt-dlp invocation - see AUDIO_ONLY handling in
# job_manager._run_download.
AUDIO_ONLY_KEY = "Audio Only"

VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".flv"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".opus", ".aac", ".flac", ".wav", ".ogg"}

# ── Encode Manager ──────────────────────────────────────────────
# Finished encodes are dropped in a subfolder of the CURRENT download
# folder, alongside the originals - matching the app's single-folder
# model rather than introducing a second global "target" concept.
CONVERTED_DIR_NAME = "Converted"

# Each codec's software (CPU) encoder is the default - hardware backends
# trade compression efficiency for speed, which cuts against "shrink the
# file while keeping quality" (see hw_encoders below, only offered if
# actually present in the local ffmpeg build).
#
# preset_kind tells the frontend/backend how to interpret the "preset"
# field, since it isn't a uniform concept across encoders:
#   "x26x"  - named presets (ultrafast..veryslow), used as-is
#   "svt"   - integer 0 (best/slowest) .. 13 (fastest), used as-is
#   "vpx"   - integer 0 (best/slowest) .. 5 (fastest) fed to -cpu-used
ENCODE_CODECS = {
    "h265": {
        "label": "H.265 / HEVC",
        "software_encoder": "libx265",
        "hw_encoders": {"amf": "hevc_amf", "nvenc": "hevc_nvenc", "qsv": "hevc_qsv"},
        "preset_kind": "x26x",
        "default_preset": "medium",
        "default_crf": 22,
        "crf_range": (14, 32),
        "container_default": "mp4",
    },
    "h264": {
        "label": "H.264 / AVC",
        "software_encoder": "libx264",
        "hw_encoders": {"amf": "h264_amf", "nvenc": "h264_nvenc", "qsv": "h264_qsv"},
        "preset_kind": "x26x",
        "default_preset": "medium",
        "default_crf": 20,
        "crf_range": (14, 30),
        "container_default": "mp4",
    },
    "av1": {
        "label": "AV1",
        "software_encoder": "libsvtav1",
        "hw_encoders": {"nvenc": "av1_nvenc", "qsv": "av1_qsv"},
        "preset_kind": "svt",
        "default_preset": "6",
        "default_crf": 30,
        "crf_range": (20, 45),
        "container_default": "mkv",
    },
    "vp9": {
        "label": "VP9",
        "software_encoder": "libvpx-vp9",
        "hw_encoders": {},
        "preset_kind": "vpx",
        "default_preset": "2",
        "default_crf": 32,
        "crf_range": (20, 45),
        "container_default": "mkv",
    },
}

# Downscale-only caps - never used to upscale a source smaller than the
# chosen cap. None means "source" (no resolution change from encoding
# itself - a force-aspect-ratio correction, if any, still applies).
RESOLUTION_CAPS = {
    "source": None,
    "2160p": 2160,
    "1440p": 1440,
    "1080p": 1080,
    "720p": 720,
    "480p": 480,
}

# Common display-aspect-ratio targets for the "force aspect ratio" fix
# (correcting sources whose pixels are actually stored squished/stretched,
# not just flagged wrong).
ASPECT_RATIOS = {
    "16:9": (16, 9),
    "4:3": (4, 3),
    "21:9": (21, 9),
}

AUDIO_MODES = {
    "copy": {"label": "Copy (no re-encode)"},
    "aac128": {"label": "Re-encode AAC 128k", "bitrate": "128k"},
    "aac192": {"label": "Re-encode AAC 192k", "bitrate": "192k"},
}

ENCODE_CONTAINERS = {"mp4", "mkv"}


