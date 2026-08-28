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
import time
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
                if _migrate_recent_dirs_to_roots(_cache):
                    _persist(_cache)
                return _cache
        except Exception:
            pass
    _cache = {}
    return _cache


def _migrate_recent_dirs_to_roots(data: dict) -> bool:
    """One-time, idempotent migration from the old capped MRU
    recent_dirs/recent_target_dirs lists to the new uncapped
    save_dir_roots/target_dir_roots lists. Runs the same add-root
    normalization each old entry would have gone through if it had
    been added under the new system, so an old list that happened to
    contain both a folder and one of its subfolders collapses down to
    just the broader one instead of carrying forward a state the new
    rules wouldn't allow going forward."""
    changed = False
    for old_key, new_key in (
        ("recent_dirs", "save_dir_roots"),
        ("recent_target_dirs", "target_dir_roots"),
    ):
        if old_key not in data:
            continue
        if new_key not in data:
            roots = []
            for p in data.get(old_key, []):
                if not isinstance(p, str) or not os.path.isdir(p):
                    continue
                try:
                    roots = _normalize_roots_for_add(roots, os.path.abspath(p))
                except ValueError:
                    continue  # already covered by a broader entry
            data[new_key] = roots
        del data[old_key]
        changed = True
    return changed


def _persist(data: dict) -> None:
    global _cache
    _cache = data
    try:
        with open(SETTINGS_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception:
        pass


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


# ── Saved root folders (download side) ─────────────────────────────
# A "root" is a folder the user explicitly added. Unlike the old
# recent-folders list, roots aren't capped and aren't themselves what
# the quick dropdown shows - the dropdown live-scans each root's actual
# subfolders on disk (see folder_tree.py) and nests them underneath it.
# Adding a root that's a subfolder of an existing root is rejected
# (it'd already be reachable by scanning the existing root); adding a
# root that's an *ancestor* of existing roots instead absorbs them,
# since scanning the new, broader root will surface those folders too.
#
# dir_last_used tracks recency per literal path - root or nested
# subfolder alike - so both the root ordering and the ordering inside
# each root's nested tree can be most-recently-used-first.
def _is_within(path: str, other: str) -> bool:
    """True if `path` is `other` itself or lives somewhere under it."""
    path = os.path.normcase(os.path.normpath(path))
    other = os.path.normcase(os.path.normpath(other))
    return path == other or path.startswith(other + os.sep)


def _normalize_roots_for_add(existing: list, new_path: str) -> list:
    """Applies the add-root rules against an existing root-path list and
    returns the updated list (existing list is left untouched). Raises
    ValueError if new_path is a subfolder of a root that's already
    saved - it's already reachable there, so adding it separately would
    just be a redundant, unnecessary duplicate entry."""
    for root in existing:
        if _is_within(new_path, root) and root != new_path:
            raise ValueError(
                f"'{new_path}' is already inside the saved folder '{root}'."
            )
    # Any existing root that's a subfolder of the new, broader path is
    # now redundant - the new root's scan will surface it on its own.
    kept = [r for r in existing if not (_is_within(r, new_path) and r != new_path)]
    if new_path not in kept:
        kept.append(new_path)
    return kept


def _touch_last_used(data: dict, path: str) -> None:
    last_used = data.setdefault("dir_last_used", {})
    last_used[path] = time.time()


def get_dir_last_used_map() -> dict:
    return dict(_load().get("dir_last_used", {}))


def _prune_stale_roots(key: str, data: dict) -> list:
    """Drops any saved root that no longer exists on disk, persisting
    the change so it doesn't just get re-checked (and silently ignored)
    forever. Returns the surviving root paths, most-recently-used
    first (matches the ordering used inside each root's nested tree)."""
    roots = data.get(key, [])
    surviving = [p for p in roots if os.path.isdir(p)]
    if surviving != roots:
        data[key] = surviving
        _persist(data)
    last_used = data.get("dir_last_used", {})
    return sorted(surviving, key=lambda p: -last_used.get(p, 0))


def get_save_dir_roots() -> list:
    return _prune_stale_roots("save_dir_roots", _load())


def add_save_dir_root(raw_path: str) -> list:
    """Validates, then adds raw_path as a new saved root folder (see
    _normalize_roots_for_add for the subfolder/ancestor rules). Returns
    the updated flat root list. Raises ValueError/OSError."""
    path = _validate_folder_path(raw_path)
    data = _load()
    existing = _prune_stale_roots("save_dir_roots", data)
    data["save_dir_roots"] = _normalize_roots_for_add(existing, path)
    _touch_last_used(data, path)
    _persist(data)
    return data["save_dir_roots"]


def remove_save_dir_root(path: str) -> list:
    """Removes a single saved root (just the entry, not the folder
    itself). Returns the updated list."""
    data = _load()
    data["save_dir_roots"] = [p for p in data.get("save_dir_roots", []) if p != path]
    _persist(data)
    return get_save_dir_roots()


def set_save_dir(raw_path: str) -> str:
    """Validates (creating if needed) and persists a new active download
    folder. If the path isn't already inside a saved root, it's
    auto-added as a new root (mirrors the old recent-folders
    convenience of remembering anywhere you've pointed the app at);
    if it's a subfolder of an existing root, only its recency is
    touched, since it's already reachable there. Returns the canonical
    absolute path. Raises ValueError/OSError on anything that isn't
    usable (e.g. pointing at a file, no permissions)."""
    path = _validate_folder_path(raw_path)
    data = _load()
    existing = _prune_stale_roots("save_dir_roots", data)
    if not any(_is_within(path, root) for root in existing):
        data["save_dir_roots"] = _normalize_roots_for_add(existing, path)
    data["save_dir"] = path
    _touch_last_used(data, path)
    _persist(data)
    return path


MAX_RECENT_TAGS = 5


def get_recent_stash_tags() -> list:
    """Rolling list of the last few Stash tags searched via Check Tag,
    most-recent-first. Used to render quick-pick chips in the Check Tag
    modal so a repeat lookup doesn't require re-typing the name."""
    data = _load()
    return list(data.get("recent_stash_tags", []))


def push_recent_stash_tag(tag_name: str) -> list:
    """Adds a tag to the rolling recent-Stash-tags list (case-insensitive
    de-dupe against any existing entry, most recent first, capped at
    MAX_RECENT_TAGS). Returns the updated list."""
    if not tag_name:
        return get_recent_stash_tags()
    data = _load()
    recent = [t for t in data.get("recent_stash_tags", []) if t.lower() != tag_name.lower()]
    recent.insert(0, tag_name)
    data["recent_stash_tags"] = recent[:MAX_RECENT_TAGS]
    _persist(data)
    return data["recent_stash_tags"]


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


def get_target_dir_roots() -> list:
    return _prune_stale_roots("target_dir_roots", _load())


def add_target_dir_root(raw_path: str) -> list:
    """Same rules as add_save_dir_root, for the target-folder side."""
    path = _validate_folder_path(raw_path)
    data = _load()
    existing = _prune_stale_roots("target_dir_roots", data)
    data["target_dir_roots"] = _normalize_roots_for_add(existing, path)
    _touch_last_used(data, path)
    _persist(data)
    return data["target_dir_roots"]


def remove_target_dir_root(path: str) -> list:
    """Removes a single saved target root. Returns the updated list."""
    data = _load()
    data["target_dir_roots"] = [p for p in data.get("target_dir_roots", []) if p != path]
    _persist(data)
    return get_target_dir_roots()


def set_target_dir(raw_path: str) -> str:
    """Validates (creating if needed) and persists the target folder used
    by 'Move to Target' / 'Move All to Target'. Same auto-root and
    recency behavior as set_save_dir - see there for the reasoning."""
    path = _validate_folder_path(raw_path)
    data = _load()
    existing = _prune_stale_roots("target_dir_roots", data)
    if not any(_is_within(path, root) for root in existing):
        data["target_dir_roots"] = _normalize_roots_for_add(existing, path)
    data["target_dir"] = path
    _touch_last_used(data, path)
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


def get_all_history_log_paths() -> list:
    """Every downloads_history.log this app knows about, one per download
    folder that's ever been used - i.e. every hashed subfolder under
    LIBRARY_DATA_DIR that actually has a log in it. Order is: the
    currently active folder's log first (if it has one), then the rest
    in no particular order - used by search_history() so a lookup checks
    the current folder first but still falls back across every other
    download folder instead of coming up empty for a file that was
    downloaded (and logged) somewhere else."""
    paths = []
    current_log = get_log_file_path()
    if os.path.exists(current_log):
        paths.append(current_log)

    try:
        entries = os.listdir(LIBRARY_DATA_DIR)
    except OSError:
        entries = []

    for entry in entries:
        folder_dir = os.path.join(LIBRARY_DATA_DIR, entry)
        if not os.path.isdir(folder_dir):
            continue
        log_path = os.path.join(folder_dir, "downloads_history.log")
        if os.path.exists(log_path) and log_path not in paths:
            paths.append(log_path)

    return paths


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
    # Whether a failed normal download automatically falls back to the
    # M3U sniffing flow. On by default to match pre-existing behavior;
    # users who find the auto-retry more annoying than useful (e.g. it
    # firing on genuinely-dead links) can turn it off and just get a
    # normal ERROR card instead.
    "auto_m3u_retry": True,
    # When on, a fetched/sniffed/playlist-provided title is submitted as
    # a download immediately rather than staged in the input box for
    # review/edit first. Applies to every download (single or playlist),
    # not just playlist items - mainly useful so a playlist batch of
    # dozens of entries doesn't need per-item review, but it's one
    # consistent on/off switch rather than a playlist-only special case.
    "auto_confirm_titles": False,
    # Monitor the Windows clipboard for newly copied HTTP(S) URLs and
    # automatically submit them for download.
    "clipboard_monitor": False,
}
VALID_QUALITIES = {"Best", "720p", "480p", "Audio Only"}


def get_download_prefs() -> dict:
    stored = _load().get(DOWNLOAD_PREFS_KEY, {})
    return {**DEFAULT_DOWNLOAD_PREFS, **stored}


def set_download_prefs(
    quality: str,
    tag_domain: bool,
    m3u_sniffer: bool,
    auto_m3u_retry: bool = True,
    auto_confirm_titles: bool = False,
    clipboard_monitor: bool = False,
) -> dict:
    prefs = {
        "quality": quality if quality in VALID_QUALITIES else DEFAULT_DOWNLOAD_PREFS["quality"],
        "tag_domain": bool(tag_domain),
        "m3u_sniffer": bool(m3u_sniffer),
        "auto_m3u_retry": bool(auto_m3u_retry),
        "auto_confirm_titles": bool(auto_confirm_titles),
        "clipboard_monitor": bool(clipboard_monitor),
    }
    data = _load()
    data[DOWNLOAD_PREFS_KEY] = prefs
    _persist(data)
    return prefs



# ── Synchronize Audio preferences ───────────────────────────────
CLIP_DURATION_KEY = "sync_clip_duration_s"
DEFAULT_CLIP_DURATION_S = 10.0

def get_sync_clip_duration() -> float:
    try:
        value = float(_load().get(CLIP_DURATION_KEY, DEFAULT_CLIP_DURATION_S))
        return value if value > 0 else DEFAULT_CLIP_DURATION_S
    except (TypeError, ValueError):
        return DEFAULT_CLIP_DURATION_S

def set_sync_clip_duration(value: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError("Clip duration must be a number.")
    if value <= 0:
        raise ValueError("Clip duration must be greater than 0 seconds.")
    data = _load()
    data[CLIP_DURATION_KEY] = value
    _persist(data)
    return value

# ── yt-dlp extra arguments (global default + per-domain overrides) ──
# Two tiers, both stored flat like download_prefs: `default_args` is a
# single string appended to every yt-dlp invocation (throttling knobs,
# --cookies-from-browser, etc. - things you want everywhere), and
# `domain_args` is a {domain: args_string} map for site-specific fixes
# (e.g. a TikTok --extractor-args workaround) keyed by the same
# normalized domain get_domain() already produces for file tagging.
# Both are appended AFTER the built-in flags in job_manager, so a
# single-value flag like -f in domain_args wins over the res-cap
# default without erroring; boolean flags simply appearing twice is
# harmless.
YTDLP_ARGS_KEY = "ytdlp_args"
DEFAULT_YTDLP_ARGS = {
    "default_args": "",
    "domain_args": {},
}


def get_ytdlp_args() -> dict:
    stored = _load().get(YTDLP_ARGS_KEY, {})
    return {
        "default_args": stored.get("default_args", ""),
        "domain_args": dict(stored.get("domain_args", {})),
    }


def set_ytdlp_default_args(args: str) -> dict:
    data = _load()
    current = data.get(YTDLP_ARGS_KEY, {})
    current["default_args"] = args or ""
    current.setdefault("domain_args", {})
    data[YTDLP_ARGS_KEY] = current
    _persist(data)
    return get_ytdlp_args()


def set_ytdlp_domain_args(domain: str, args: str) -> dict:
    """Adds/updates the args rule for `domain`. An empty/whitespace-only
    `args` removes the rule entirely rather than storing a blank
    entry - same "empty means delete" convention as the rest of the
    settings surface, so callers don't need a separate delete path for
    the common "clear this field and save" interaction."""
    domain = (domain or "").strip().lower()
    if not domain:
        raise ValueError("Domain can't be empty.")

    data = _load()
    current = data.get(YTDLP_ARGS_KEY, {})
    current.setdefault("default_args", "")
    domain_args = current.setdefault("domain_args", {})

    if args and args.strip():
        domain_args[domain] = args.strip()
    else:
        domain_args.pop(domain, None)

    data[YTDLP_ARGS_KEY] = current
    _persist(data)
    return get_ytdlp_args()


def delete_ytdlp_domain_args(domain: str) -> dict:
    domain = (domain or "").strip().lower()
    data = _load()
    current = data.get(YTDLP_ARGS_KEY, {})
    current.setdefault("default_args", "")
    domain_args = current.setdefault("domain_args", {})
    domain_args.pop(domain, None)
    data[YTDLP_ARGS_KEY] = current
    _persist(data)
    return get_ytdlp_args()


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
