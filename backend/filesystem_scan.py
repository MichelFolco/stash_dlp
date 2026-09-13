"""Reconciles the saved queue (queue.json) against what's actually on disk.
Ported from YtdlpManagerApp.parse_and_render_filesystem(), minus the Qt
widget creation - this returns plain dicts the job manager can turn into
snapshot entries. Detects both video and standalone audio files (ripped
via "Extract Audio", downloaded as "Audio Only", or just dropped into the
folder by hand) and flags each with is_audio so the ledger can label them.

Also detects files renamed or moved outside the app (file explorer,
another program) via a content fingerprint, so a tracked entry's
download-history association follows it to the new name instead of
being dropped - see the "rename detection" section below and
fingerprints.py.
"""
import os
import re

from config import VIDEO_EXTENSIONS, AUDIO_EXTENSIONS
from fingerprints import load_fingerprint_index, save_fingerprint_index, update_fingerprint
from settings import get_save_dir, migrate_old_local_data_dir, migrate_legacy_layout, get_download_prefs
from storage import load_saved_queue, save_queue_to_disk, write_to_history_log
from ytdlp_utils import format_file_size, get_downloaded_file_size


def scan_filesystem():
    """Returns (done_jobs, renamed_pairs) and persists the reconciled
    queue (and, if rename detection is on, the fingerprint index) back
    to disk. done_jobs is a list of completed-job dicts (filename, url,
    res_cap, status, file_size, is_audio, ...). renamed_pairs is a list
    of {"old": ..., "new": ...} for any file this scan recognized as
    renamed/moved outside the app, so callers can notify connected
    clients. Note: DOWNLOADING entries left over from an unclean
    shutdown are kept in the persisted queue but intentionally not
    surfaced as cards here - this mirrors the desktop app's existing
    behavior."""
    save_dir = get_save_dir()
    if not os.path.exists(save_dir):
        return [], []

    migrate_old_local_data_dir()
    migrate_legacy_layout()

    completed_files = {}  # stem -> is_audio
    stem_paths = {}  # stem -> full path, for fingerprinting
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
            stem_paths[stem] = full_path
        elif ext in AUDIO_EXTENSIONS:
            completed_files[stem] = True
            stem_paths[stem] = full_path

    saved_queue = load_saved_queue()
    updated_tracker = {}
    done_jobs = []

    # ── Rename detection ──────────────────────────────────────────
    # Before reconciling, figure out whether any file that just "went
    # missing" (a tracked, DONE entry whose filename is no longer on
    # disk) actually just reappeared under a different name (a file on
    # disk with no tracked entry at all). If so, redirect the old
    # entry's history/metadata onto the new name instead of letting the
    # normal logic below drop the old one and add the new one as a
    # blank "Unknown" job.
    detect_renames = get_download_prefs().get("detect_renames", True)
    fingerprint_index = load_fingerprint_index() if detect_renames else {}
    fingerprint_index_dirty = False
    renamed_pairs = []  # [{"old": ..., "new": ...}]
    # new_filename -> (old_filename, old queue.json info dict)
    redirect_new_from_old = {}

    if detect_renames:
        orphan_candidates = {
            fname: info for fname, info in saved_queue.items()
            if fname not in completed_files and info.get("status") == "DONE"
        }
        newcomer_stems = [stem for stem in completed_files if stem not in saved_queue]

        if orphan_candidates and newcomer_stems:
            # Only orphans with a previously-recorded fingerprint are
            # matchable - that fingerprint had to be captured while the
            # file still existed on disk under its old name (recorded on
            # download completion, on in-app rename, and via the
            # backfill pass further down for anything from before this
            # feature existed).
            orphans_by_size = {}
            for fname in orphan_candidates:
                fp = fingerprint_index.get(fname)
                if fp:
                    orphans_by_size.setdefault(fp["size"], []).append(fname)

            used_orphans = set()
            for stem in newcomer_stems:
                path = stem_paths.get(stem)
                if not path:
                    continue
                try:
                    size = os.path.getsize(path)
                except OSError:
                    continue

                same_size_orphans = [f for f in orphans_by_size.get(size, []) if f not in used_orphans]
                if not same_size_orphans:
                    continue

                if update_fingerprint(fingerprint_index, stem, path):
                    fingerprint_index_dirty = True
                new_hash = fingerprint_index.get(stem, {}).get("quick_hash")
                if not new_hash:
                    continue

                matches = [
                    f for f in same_size_orphans
                    if fingerprint_index.get(f, {}).get("quick_hash") == new_hash
                ]
                if len(matches) == 1:
                    old_name = matches[0]
                    used_orphans.add(old_name)
                    redirect_new_from_old[stem] = (old_name, orphan_candidates[old_name])
                    renamed_pairs.append({"old": old_name, "new": stem})
                # len(matches) > 1: two or more orphans share this exact
                # content (duplicate files) - ambiguous which one this
                # is, so leave it alone rather than risk a wrong match.
                # Falls through to the normal "dropped" / "new" handling
                # below, same as if rename detection found nothing.

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
                    "fully_played": info.get("fully_played", False),
                    "width": info.get("width", 0),
                    "height": info.get("height", 0),
                    "duration": info.get("duration", 0),
                    "ext": info.get("ext", ""),
                    "video_codec": info.get("video_codec", ""),
                    "audio_codec": info.get("audio_codec", ""),
                }
            )
            # Backfill / keep-fresh: cheap no-op if size+mtime already
            # match what's stored, so this only actually costs I/O for
            # files that are new to the index or have changed.
            if detect_renames and filename in stem_paths:
                if update_fingerprint(fingerprint_index, filename, stem_paths[filename]):
                    fingerprint_index_dirty = True
        else:
            if status == "DONE":
                # The file's gone under this name - deleted, moved, or
                # (if rename detection matched it above) renamed. A
                # rename is handled separately below; anything else
                # drops, since a DONE entry means nothing without the
                # file it points to.
                pass
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
            else:
                # DOWNLOADING, QUEUED, or any future in-flight status -
                # no file to reconcile against yet, so just keep it as
                # persisted. Denylist, not allowlist: DONE is the only
                # status this function actively drops (above), so a
                # new status added later is preserved by default
                # instead of needing its own branch here to avoid being
                # silently wiped on the next refresh - which is exactly
                # what happened to QUEUED before this fix. (This branch
                # never has to add anything to done_jobs - job_manager's
                # own in-memory self.jobs is what keeps DOWNLOADING and
                # QUEUED surfaced as cards after a refresh.)
                updated_tracker[filename] = info

    # Apply any renames matched above: carry the old entry's URL and
    # metadata over to the new filename, log a "RENAMED from" line
    # under the new name (same log line shape an in-app rename already
    # produces), and drop the old key from the fingerprint index since
    # that name no longer exists on disk.
    for new_name, (old_name, old_info) in redirect_new_from_old.items():
        is_audio = completed_files[new_name]
        new_info = dict(old_info)
        new_info["is_audio"] = is_audio
        updated_tracker[new_name] = new_info

        url = old_info.get("url", "")
        done_jobs.append(
            {
                "filename": new_name,
                "url": url,
                "res_cap": old_info.get("res_cap", "720p"),
                "status": "DONE",
                "file_size": old_info.get("file_size", ""),
                "is_audio": is_audio,
                "playback_position": old_info.get("playback_position", 0),
                "fully_played": old_info.get("fully_played", False),
                "width": old_info.get("width", 0),
                "height": old_info.get("height", 0),
                "duration": old_info.get("duration", 0),
                "ext": old_info.get("ext", ""),
                "video_codec": old_info.get("video_codec", ""),
                "audio_codec": old_info.get("audio_codec", ""),
            }
        )
        write_to_history_log(new_name, url, f"RENAMED from {old_name}")

        if fingerprint_index.pop(old_name, None) is not None:
            fingerprint_index_dirty = True

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
            # Genuinely new/untracked file (not a detected rename) -
            # fingerprint it too so a future rename of THIS file can
            # still be recognized later.
            if detect_renames and stem in stem_paths:
                if update_fingerprint(fingerprint_index, stem, stem_paths[stem]):
                    fingerprint_index_dirty = True

    if detect_renames and fingerprint_index_dirty:
        save_fingerprint_index(fingerprint_index)

    save_queue_to_disk(updated_tracker)
    return done_jobs, renamed_pairs
