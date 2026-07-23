"""Shared configuration and paths.

The download folder is user-changeable at runtime (see settings.py /
the "Download Folder..." option in the app's logo menu), so it's no
longer a fixed constant here. STASH_DLP_SAVE_DIR still works as the
initial default the first time the app runs, before any folder has
been chosen via the UI.
"""
import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent

# Where the app's own bookkeeping lives - this location is fixed
# regardless of which folder downloads currently go to.
SETTINGS_JSON_PATH = os.path.join(str(PROJECT_ROOT), "_app_settings.json")

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

