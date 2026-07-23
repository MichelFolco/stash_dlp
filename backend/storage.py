"""Persistence layer: the saved job queue (queue.json) and the plain-text
history log. Schema is kept identical to the PyQt6 desktop app
(stash_dlp.py) on purpose, so the two can share a save folder without
conflicting.
"""
import json
import os
import re
from datetime import datetime

from settings import get_queue_json_path, get_log_file_path


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
