"""The download folder is the one piece of config a user can change at
runtime (via the "Download Folder..." option in the app's logo menu).

Per-folder app data (queue.json, encode queue, history log, thumbnails)
used to live inside each download folder itself. It now lives centrally
under config.LIBRARY_DATA_DIR instead, one subfolder per download folder
ever used - see get_data_dir() / _folder_data_dir(). This was originally
kept identical to the old PyQt6 desktop app's layout so the two could
share a folder without conflicting; that app is retired now, so there's
no compatibility reason left to scatter data across every folder a user
has ever pointed this app at.
"""
import hashlib
import json
import os
import shutil
import uuid

from config import (
    SETTINGS_JSON_PATH,
    DEFAULT_SAVE_DIR,
    VIDEO_EXTENSIONS,
    AUDIO_EXTENSIONS,
    CONVERTED_DIR_NAME,
    LIBRARY_DATA_DIR,
)

_cache = None


def _load() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    if os.path.exists(SETTINGS_JSON_PATH):
        try:
            with open(SETTINGS_JSON_PATH, "r", encoding="utf-8") as f:
                _cache = json.load(f)
                return _cache
        except Exception:
            pass
    _cache = {}
    return _cache


def _persist(data: dict) -> None:
    global _cache
    _cache = data
    try:
        with open(SETTINGS_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception:
        pass


MAX_RECENT = 8


def _validate_folder_path(raw_path: str) -> str:
    """Shared validation for any folder path the user provides (download
    folder or target folder): non-empty, not pointing at a file, creatable,
    and writable. Returns the canonical absolute path."""
    if not raw_path or not raw_path.strip():
        raise ValueError("Folder path can't be empty.")

    path = os.path.abspath(os.path.expanduser(raw_path.strip()))

    if os.path.exists(path) and not os.path.isdir(path):
        raise ValueError(f"'{path}' exists and is not a folder.")

    os.makedirs(path, exist_ok=True)

    if not os.access(path, os.W_OK):
        raise ValueError(f"No write permission for '{path}'.")

    return path


def get_save_dir() -> str:
    path = _load().get("save_dir") or DEFAULT_SAVE_DIR
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass
    return path


def get_recent_dirs() -> list:
    data = _load()
    recent = data.get("recent_dirs", [])
    # Filter out folders that no longer exist so the list stays useful
    return [p for p in recent if os.path.isdir(p)]


def remove_recent_dir(path: str) -> list:
    """Removes a single entry from the recent-download-folders list
    (doesn't touch the folder itself, just the history entry). Returns
    the updated list."""
    data = _load()
    data["recent_dirs"] = [p for p in data.get("recent_dirs", []) if p != path]
    _persist(data)
    return get_recent_dirs()


def _push_recent(path: str, data: dict) -> None:
    recent = [p for p in data.get("recent_dirs", []) if p != path]
    recent.insert(0, path)
    data["recent_dirs"] = recent[:MAX_RECENT]


def set_save_dir(raw_path: str) -> str:
    """Validates (creating if needed) and persists a new download folder.
    Returns the canonical absolute path. Raises ValueError/OSError on
    anything that isn't usable (e.g. pointing at a file, no permissions)."""
    path = _validate_folder_path(raw_path)
    data = _load()
    data["save_dir"] = path
    _push_recent(path, data)
    _persist(data)
    return path


# ── Target folder ("Move to Target") ──────────────────────────────
# Global rather than per-download-folder - a persistent destination you
# move finished files to, independent of which download folder is active.
def get_target_dir() -> str:
    """Returns the configured target folder, or '' if none set yet."""
    path = _load().get("target_dir", "")
    if path:
        try:
            os.makedirs(path, exist_ok=True)
        except Exception:
            pass
    return path


def get_recent_target_dirs() -> list:
    data = _load()
    recent = data.get("recent_target_dirs", [])
    return [p for p in recent if os.path.isdir(p)]


def remove_recent_target_dir(path: str) -> list:
    """Removes a single entry from the recent-target-folders list.
    Returns the updated list."""
    data = _load()
    data["recent_target_dirs"] = [p for p in data.get("recent_target_dirs", []) if p != path]
    _persist(data)
    return get_recent_target_dirs()


def _push_recent_target(path: str, data: dict) -> None:
    recent = [p for p in data.get("recent_target_dirs", []) if p != path]
    recent.insert(0, path)
    data["recent_target_dirs"] = recent[:MAX_RECENT]


def set_target_dir(raw_path: str) -> str:
    """Validates (creating if needed) and persists the target folder used
    by 'Move to Target' / 'Move All to Target'."""
    path = _validate_folder_path(raw_path)
    data = _load()
    data["target_dir"] = path
    _push_recent_target(path, data)
    _persist(data)
    return path


# ── External programs ("Open With...") ────────────────────────────
# Global rather than per-download-folder, like target_dir - a fixed set
# of tools (VLC, an editor, whatever) you launch completed files with,
# independent of which download folder is currently active.
EXTERNAL_PROGRAMS_KEY = "external_programs"


def _validate_program_path(raw_path: str) -> str:
    """Shared validation for an external program's executable path:
    non-empty, exists, and points at a file (not a folder). Deliberately
    stricter than _validate_folder_path (no auto-create) since a program
    path that doesn't exist yet is just a mistake, not a folder to make."""
    if not raw_path or not raw_path.strip():
        raise ValueError("Program path can't be empty.")

    path = os.path.abspath(os.path.expanduser(raw_path.strip()))

    if not os.path.exists(path):
        raise ValueError(f"'{path}' doesn't exist.")
    if not os.path.isfile(path):
        raise ValueError(f"'{path}' is not a file.")

    return path


def get_external_programs() -> list:
    return _load().get(EXTERNAL_PROGRAMS_KEY, [])


def get_external_program(program_id: str):
    for prog in get_external_programs():
        if prog.get("id") == program_id:
            return prog
    return None


def add_external_program(name: str, path: str, args: str = "") -> list:
    """Validates and appends a new external program entry. Returns the
    updated list of all programs (id, name, path, args)."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Program name can't be empty.")
    validated_path = _validate_program_path(path)

    data = _load()
    programs = data.get(EXTERNAL_PROGRAMS_KEY, [])
    programs.append({
        "id": uuid.uuid4().hex,
        "name": name,
        "path": validated_path,
        "args": args or "",
    })
    data[EXTERNAL_PROGRAMS_KEY] = programs
    _persist(data)
    return programs


def update_external_program(program_id: str, name: str, path: str, args: str = "") -> list:
    """Validates and updates an existing entry in place (matched by id,
    not name, so renaming doesn't create a duplicate). Returns the
    updated list of all programs."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Program name can't be empty.")
    validated_path = _validate_program_path(path)

    data = _load()
    programs = data.get(EXTERNAL_PROGRAMS_KEY, [])
    for prog in programs:
        if prog.get("id") == program_id:
            prog["name"] = name
            prog["path"] = validated_path
            prog["args"] = args or ""
            break
    else:
        raise ValueError("That program no longer exists.")
    data[EXTERNAL_PROGRAMS_KEY] = programs
    _persist(data)
    return programs


def delete_external_program(program_id: str) -> list:
    data = _load()
    programs = [p for p in data.get(EXTERNAL_PROGRAMS_KEY, []) if p.get("id") != program_id]
    data[EXTERNAL_PROGRAMS_KEY] = programs
    _persist(data)
    return programs


DATA_DIR_NAME = "stash_dlp_data"
THUMBNAILS_DIR_NAME = ".thumbnails"


def _folder_data_key(folder: str) -> str:
    """Stable, filesystem-safe identifier for a download folder, used as
    its subfolder name under LIBRARY_DATA_DIR. Hashed rather than a
    sanitized copy of the real path - Windows path-length limits and
    special characters make raw/sanitized paths risky as folder names,
    especially once nested under another root. Normalized so the same
    folder always maps to the same key regardless of trailing slashes
    or drive-letter case."""
    normalized = os.path.normcase(os.path.normpath(os.path.abspath(folder)))
    return hashlib.sha1(normalized.encode("utf-8", errors="replace")).hexdigest()[:12]


def _folder_data_dir(folder: str) -> str:
    """Creates (if needed) and returns the central data dir for a given
    download folder. Also drops a small _folder.txt marker with the real
    path inside it, purely so the otherwise-opaque hashed folder name is
    still identifiable by hand later if you go looking."""
    path = os.path.join(LIBRARY_DATA_DIR, _folder_data_key(folder))
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass
    marker = os.path.join(path, "_folder.txt")
    if not os.path.exists(marker):
        try:
            with open(marker, "w", encoding="utf-8") as f:
                f.write(os.path.abspath(folder))
        except Exception:
            pass
    return path


def get_data_dir() -> str:
    """Central per-folder app data location for the CURRENTLY active
    download folder (queue.json, encode queue, history log, thumbnails)."""
    return _folder_data_dir(get_save_dir())


def get_thumbnails_dir() -> str:
    path = os.path.join(get_data_dir(), THUMBNAILS_DIR_NAME)
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass
    return path


def get_queue_json_path() -> str:
    return os.path.join(get_data_dir(), "_download_queue.json")


def get_log_file_path() -> str:
    return os.path.join(get_data_dir(), "downloads_history.log")


def get_log_file_path_for_folder(folder: str) -> str:
    """Same layout as get_log_file_path(), but for an arbitrary folder
    rather than the currently active save_dir. Used by external callers
    (/api/history-lookup) that want a specific folder's history without
    changing which folder the running server is pointed at. Deliberately
    does NOT create anything - a passive lookup for a folder shouldn't
    have the side effect of registering it in the central store. If that
    folder was ever actually used via this app, its data dir (and this
    path) already exists; if not, the caller's own os.path.exists check
    just finds nothing, same as before."""
    return os.path.join(LIBRARY_DATA_DIR, _folder_data_key(folder), "downloads_history.log")


# ── Encode Manager ──────────────────────────────────────────────
def get_converted_dir() -> str:
    """Where finished encodes land - a subfolder of the CURRENT download
    folder, alongside the originals. Recomputed on every call (not cached)
    since it depends on get_save_dir(), which can change at runtime."""
    path = os.path.join(get_save_dir(), CONVERTED_DIR_NAME)
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass
    return path


def get_encode_queue_json_path() -> str:
    return os.path.join(get_data_dir(), "_encode_queue.json")


# ── Download preferences (Max Res / Tag Domain / M3U Sniffer) ─────
# Small, global UI toggles from the settings flyout that have nothing to
# do with which download folder is active, so - like target_dir and the
# external programs list - they're stored flat in _app_settings.json
# rather than per-folder.
DOWNLOAD_PREFS_KEY = "download_prefs"
DEFAULT_DOWNLOAD_PREFS = {
    "quality": "720p",
    "tag_domain": True,
    "m3u_sniffer": False,
}
VALID_QUALITIES = {"Best", "720p", "480p", "Audio Only"}


def get_download_prefs() -> dict:
    stored = _load().get(DOWNLOAD_PREFS_KEY, {})
    return {**DEFAULT_DOWNLOAD_PREFS, **stored}


def set_download_prefs(quality: str, tag_domain: bool, m3u_sniffer: bool) -> dict:
    prefs = {
        "quality": quality if quality in VALID_QUALITIES else DEFAULT_DOWNLOAD_PREFS["quality"],
        "tag_domain": bool(tag_domain),
        "m3u_sniffer": bool(m3u_sniffer),
    }
    data = _load()
    data[DOWNLOAD_PREFS_KEY] = prefs
    _persist(data)
    return prefs


def migrate_old_local_data_dir() -> None:
    """One-time, idempotent, best-effort migration from the old layout
    (<save_dir>/stash_dlp_data/...) to the central library_data store.
    Safe to call every time the app boots or the folder changes - a
    no-op once migrated. Runs before migrate_legacy_layout(), which
    still handles the older (pre-stash_dlp_data) loose-file layout and
    now lands its output in the new central location automatically,
    since it goes through get_data_dir()/get_thumbnails_dir() too."""
    save_dir = get_save_dir()
    old_dir = os.path.join(save_dir, DATA_DIR_NAME)
    if not os.path.isdir(old_dir):
        return

    new_dir = get_data_dir()

    for name in ("_download_queue.json", "_encode_queue.json", "downloads_history.log"):
        old_path = os.path.join(old_dir, name)
        new_path = os.path.join(new_dir, name)
        if os.path.isfile(old_path) and not os.path.exists(new_path):
            try:
                shutil.move(old_path, new_path)
            except OSError:
                pass

    old_thumbs = os.path.join(old_dir, THUMBNAILS_DIR_NAME)
    if os.path.isdir(old_thumbs):
        new_thumbs = get_thumbnails_dir()
        try:
            thumb_names = os.listdir(old_thumbs)
        except OSError:
            thumb_names = []
        for fname in thumb_names:
            src = os.path.join(old_thumbs, fname)
            dst = os.path.join(new_thumbs, fname)
            if os.path.isfile(src) and not os.path.exists(dst):
                try:
                    shutil.move(src, dst)
                except OSError:
                    pass
        try:
            os.rmdir(old_thumbs)
        except OSError:
            pass  # not empty (a move above failed) - leave it, harmless

    try:
        os.rmdir(old_dir)
    except OSError:
        pass  # not empty - leave it rather than risk losing anything


def migrate_legacy_layout() -> None:
    """One-time, idempotent, best-effort migration for folders that still
    have queue.json/history log/thumbnails sitting loose at the download
    folder's root from before stash_dlp_data existed. Safe to call every
    time the app boots or the folder changes - it's a no-op once migrated."""
    save_dir = get_save_dir()
    data_dir = get_data_dir()
    thumbs_dir = get_thumbnails_dir()

    legacy_queue = os.path.join(save_dir, "_download_queue.json")
    new_queue = os.path.join(data_dir, "_download_queue.json")
    if os.path.isfile(legacy_queue) and not os.path.exists(new_queue):
        try:
            shutil.move(legacy_queue, new_queue)
        except OSError:
            pass

    legacy_log = os.path.join(save_dir, "downloads_history.log")
    new_log = os.path.join(data_dir, "downloads_history.log")
    if os.path.isfile(legacy_log) and not os.path.exists(new_log):
        try:
            shutil.move(legacy_log, new_log)
        except OSError:
            pass

    try:
        entries = os.listdir(save_dir)
    except OSError:
        return

    media_stems = set()
    for fname in entries:
        full_path = os.path.join(save_dir, fname)
        if not os.path.isfile(full_path):
            continue
        stem, ext = os.path.splitext(fname)
        if ext.lower() in VIDEO_EXTENSIONS or ext.lower() in AUDIO_EXTENSIONS:
            media_stems.add(stem)

    for stem in media_stems:
        dest = os.path.join(thumbs_dir, stem + ".jpg")
        for legacy_name in (stem + ".jpg", stem + ".thumb.jpg"):
            legacy_path = os.path.join(save_dir, legacy_name)
            if not os.path.isfile(legacy_path):
                continue
            if os.path.exists(dest):
                # Already migrated (or a duplicate) - just clean up.
                try:
                    os.remove(legacy_path)
                except OSError:
                    pass
            else:
                try:
                    shutil.move(legacy_path, dest)
                except OSError:
                    pass
