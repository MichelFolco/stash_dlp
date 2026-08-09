"""Persistence layer: the saved job queue (queue.json) and the plain-text
history log. Schema is kept identical to the PyQt6 desktop app
(stash_dlp.py) on purpose, so the two can share a save folder without
conflicting.
"""
import json
import os
import re
from datetime import datetime

from settings import get_queue_json_path, get_log_file_path, get_log_file_path_for_folder


def load_saved_queue() -> dict:
    path = get_queue_json_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_queue_to_disk(queue_data: dict) -> None:
    try:
        with open(get_queue_json_path(), "w", encoding="utf-8") as f:
            json.dump(queue_data, f, indent=4, ensure_ascii=False)
    except Exception:
        pass


def write_to_history_log(filename: str, url: str, status: str) -> None:
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"{timestamp} - [{status}] - {filename} - {url}\n"
        with open(get_log_file_path(), "a", encoding="utf-8") as log_file:
            log_file.write(log_line)
    except Exception:
        pass


_LOG_PATTERN = re.compile(r"^.*? - \[.*?\] - (.*?) - (https?://.*)$", re.IGNORECASE)
_FULL_LOG_PATTERN = re.compile(r"^(.*?) - \[(.*?)\] - (.*?) - (https?://.*)$", re.IGNORECASE)


def search_history(query: str) -> str:
    """Mirrors execute_history_search from the desktop app: returns the
    first matching URL for a (possibly messy) filename/URL query, or ''."""
    from urllib.parse import unquote

    query = (query or "").strip()
    if not query:
        return ""

    query = unquote(query)
    if query.lower().startswith("file:///"):
        query = query.split("/")[-1]
    if "." in query:
        query = query.rsplit(".", 1)[0]

    log_path = get_log_file_path()
    if not os.path.exists(log_path):
        return ""

    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return ""

    for line in lines:
        match = _LOG_PATTERN.match(line.strip())
        if match:
            filename_part = match.group(1).strip()
            url_part = match.group(2).strip()
            if query.lower() in filename_part.lower():
                return url_part
    return ""


class HistoryLookupError(Exception):
    """Raised by lookup_history_in_folder() when the folder or a matching
    entry can't be found, so the API layer can turn it into a 404 with
    a specific reason."""


def lookup_history_in_folder(folder: str, filename: str) -> str:
    """For external callers: given a download folder and a filename,
    return the exact matching URL from that folder's history log.
    Unlike search_history()/get_log_file_path(), this does NOT depend on
    - or change - the server's currently active save_dir; it resolves
    that folder's entry in the central library_data store directly (see
    settings.get_log_file_path_for_folder()), so it works for any folder
    the caller names, not just the one currently open in the app.

    Matching is exact (case-insensitive, extension-insensitive) rather
    than substring, since this is meant for programmatic callers passing
    a known filename rather than a messy pasted query.

    Raises HistoryLookupError if the folder doesn't exist, has no
    stash_dlp history log, or has no entry matching that filename.
    """
    from urllib.parse import unquote

    folder = (folder or "").strip()
    filename = (filename or "").strip()
    if not folder or not filename:
        raise HistoryLookupError("folder and filename are required")

    if not os.path.isdir(folder):
        raise HistoryLookupError(f"folder does not exist: {folder}")

    log_path = get_log_file_path_for_folder(folder)
    if not os.path.exists(log_path):
        raise HistoryLookupError(f"no stash_dlp history found for folder: {folder}")

    query = unquote(filename)
    if query.lower().startswith("file:///"):
        query = query.split("/")[-1]
    if "." in query:
        query = query.rsplit(".", 1)[0]
    query = query.strip().lower()

    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        raise HistoryLookupError(f"could not read history log: {e}")

    for line in lines:
        match = _LOG_PATTERN.match(line.strip())
        if not match:
            continue
        filename_part = match.group(1).strip()
        url_part = match.group(2).strip()
        candidate = filename_part
        if "." in candidate:
            candidate = candidate.rsplit(".", 1)[0]
        if candidate.strip().lower() == query:
            return url_part

    raise HistoryLookupError(f"no history entry matching filename: {filename}")


def get_history_entries() -> list:
    """Parses the full plain-text history log into structured entries -
    one per past download attempt, oldest first (file order). Used by
    Search History Mode to populate the ledger with every download
    that's ever been logged, regardless of whether the file still
    exists on disk (it may have been moved, renamed, or deleted since)."""
    log_path = get_log_file_path()
    if not os.path.exists(log_path):
        return []

    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return []

    entries = []
    for line in lines:
        match = _FULL_LOG_PATTERN.match(line.strip())
        if not match:
            continue
        timestamp, status, filename, url = match.groups()
        entries.append({
            "timestamp": timestamp.strip(),
            "status": status.strip(),
            "filename": filename.strip(),
            "url": url.strip(),
        })
    return entries


def delete_history_entry(timestamp: str, filename: str, url: str) -> bool:
    """Removes a single matching line from the history log (the record
    only - this never touches the actual downloaded file). Matches on
    all three fields to avoid accidentally removing the wrong entry
    when a filename was downloaded more than once. Removes at most one
    matching line, so repeated entries need repeated deletes."""
    log_path = get_log_file_path()
    if not os.path.exists(log_path):
        return False

    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return False

    removed = False
    kept_lines = []
    for line in lines:
        if not removed:
            match = _FULL_LOG_PATTERN.match(line.strip())
            if (
                match
                and match.group(1).strip() == timestamp
                and match.group(3).strip() == filename
                and match.group(4).strip() == url
            ):
                removed = True
                continue
        kept_lines.append(line)

    if not removed:
        return False

    try:
        with open(log_path, "w", encoding="utf-8") as f:
            f.writelines(kept_lines)
    except Exception:
        return False
    return True

