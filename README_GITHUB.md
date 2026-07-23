# >stash_dlp

Stash DLP is a self-hosted web app for downloading and managing video and audio with yt-dlp, accessible from any browser on your network - phone included, over Tailscale or your LAN. It replaces a desktop-only tool with one you can drive from anywhere, while keeping full control of where your files live.

## Features

- Paste a link, confirm the fetched title, and download with live progress and a thumbnail
- Video, audio-only, or extract-audio-from-an-existing-video, all clearly labeled in the ledger
- In-browser playback for both video and audio, with resume-where-you-left-off
- Filterable, sortable ledger (name, size, date added)
- Per-item menu: play, rename, delete, copy link/filename, move to a separate target folder
- "Move All to Target" to batch-move everything completed in one go
- M3U8 sniffer mode and a searchable download history for re-finding old links
- Configurable download folder and target folder, each with a native folder browser and recent-folders list
- Runs in the Windows system tray, can launch at boot, and can restart itself from the UI

## Installation

**Requirements:** Python 3.10+, [yt-dlp](https://github.com/yt-dlp/yt-dlp) on your PATH, `ffmpeg` on your PATH (thumbnails, audio extraction), optional `playwright` (M3U8 sniffer mode).

```bash
git clone <your-repo-url> stash_dlp_web
cd stash_dlp_web
pip install -r requirements.txt
python backend/main.py
```

Then open **http://127.0.0.1:8722** in a browser.

For the system tray version (Windows): `pip install pystray Pillow`, then run `tray_launcher.py` with `pythonw.exe` instead of `python.exe` so no console window appears. See the full README for boot-startup and Tailscale/LAN setup instructions.
