"""Reconciles the saved queue (queue.json) against what's actually on disk.
Ported from YtdlpManagerApp.parse_and_render_filesystem(), minus the Qt
widget creation - this returns plain dicts the job manager can turn into
snapshot entries. Detects both video and standalone audio files (ripped
via "Extract Audio", downloaded as "Audio Only", or just dropped into the
folder by hand) and flags each with is_audio so the ledger can label them.
"""
import os
import re

from config import VIDEO_EXTENSIONS, AUDIO_EXTENSIONS
from settings import get_save_dir, migrate_old_local_data_dir, migrate_legacy_layout
from storage import load_saved_queue, save_queue_to_disk
from ytdlp_utils import format_file_size, get_downloaded_file_size


def scan_filesystem():
    """Returns a list of completed-job dicts (filename, url, res_cap,
    status, file_size, is_audio) and persists the reconciled queue back
    to disk. Note: DOWNLOADING entries left over from an unclean shutdown
    are kept in the persisted queue but intentionally not surfaced as
    cards here - this mirrors the desktop app's existing behavior."""
    save_dir = get_save_dir()
    if not os.path.exists(save_dir):
        return []

    migrate_old_local_data_dir()
    migrate_legacy_layout()

    completed_files = {}  # stem -> is_audio
    for fname in os.listdir(save_dir):
        full_path = os.path.join(save_dir, fname)
        if not os.path.isfile(full_path):
            continue  # skips stash_dlp_data/ itself, and anything else non-file
        stem, ext = os.path.splitext(fname)
        ext = ext.lower()
        if ext in VIDEO_EXTENSIONS:
            # Skip yt-dlp's intermediate per-format fragment files
            # (e.g. "Foo.f137.mp4") that exist mid-download, pre-merge.
            if re.search(r"\.f\d+$", stem):
                continue
            completed_files[stem] = False
        elif ext in AUDIO_EXTENSIONS:
            completed_files[stem] = True

    saved_queue = load_saved_queue()
    updated_tracker = {}
    done_jobs = []

    for filename, info in list(saved_queue.items()):
        url = info.get("url", "")
        res_cap = info.get("res_cap", "720p")
        status = info.get("status", "ERROR")

        if filename in completed_files:
            is_audio = completed_files[filename]
            file_size_str = info.get("file_size", "")
            if not file_size_str:
                size_bytes = get_downloaded_file_size(filename)
                if size_bytes is not None:
                    file_size_str = format_file_size(size_bytes)
                    info["file_size"] = file_size_str
            info["is_audio"] = is_audio
            updated_tracker[filename] = info
            done_jobs.append(
                {
                    "filename": filename,
                    "url": url,
                    "res_cap": res_cap,
                    "status": "DONE",
                    "file_size": file_size_str,
                    "is_audio": is_audio,
                    "playback_position": info.get("playback_position", 0),
                    "width": info.get("width", 0),
                    "height": info.get("height", 0),
                    "duration": info.get("duration", 0),
                    "ext": info.get("ext", ""),
                    "video_codec": info.get("video_codec", ""),
                    "audio_codec": info.get("audio_codec", ""),
                }
            )
        else:
            if status == "DOWNLOADING":
                updated_tracker[filename] = info
            elif status in ("ERROR", "CANCELLED"):
                # Failed/cancelled downloads stay in the ledger until the
                # user explicitly deletes them via the card - even if no
                # file ever landed on disk (or it was cleaned up since).
                # This only preserves entries already tracked in
                # queue.json going forward; it never reconstructs
                # anything from the plain-text history log, so nothing
                # from before this behavior existed comes back.
                updated_tracker[filename] = info
                done_jobs.append(
                    {
                        "filename": filename,
                        "url": url,
                        "res_cap": res_cap,
                        "status": status,
                        "file_size": info.get("file_size", ""),
                        "is_audio": info.get("is_audio", False),
                        "playback_position": info.get("playback_position", 0),
                        "width": info.get("width", 0),
                        "height": info.get("height", 0),
                        "duration": info.get("duration", 0),
                        "ext": info.get("ext", ""),
                        "video_codec": info.get("video_codec", ""),
                        "audio_codec": info.get("audio_codec", ""),
                    }
                )

    for stem in sorted(completed_files):
        if stem not in updated_tracker:
            is_audio = completed_files[stem]
            size_bytes = get_downloaded_file_size(stem)
            file_size_str = format_file_size(size_bytes) if size_bytes is not None else ""
            updated_tracker[stem] = {
                "url": "",
                "res_cap": "Unknown",
                "status": "DONE",
                "file_size": file_size_str,
                "is_audio": is_audio,
            }
            done_jobs.append(
                {
                    "filename": stem,
                    "url": "",
                    "res_cap": "Unknown",
                    "status": "DONE",
                    "file_size": file_size_str,
                    "is_audio": is_audio,
                    "video_codec": "",
                    "audio_codec": "",
                }
            )

    save_queue_to_disk(updated_tracker)
    return done_jobs
