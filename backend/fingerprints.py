"""Lightweight content-fingerprint index used to recognize a file that's
been renamed or moved outside the app (file explorer, another program,
etc.), so its download-history entry can follow it to the new name
instead of being silently dropped. See filesystem_scan.py for how this
gets used during a scan.

Deliberately NOT a full-file hash - see compute_quick_fingerprint() -
and stored in its own sidecar JSON file per download folder (see
settings.get_fingerprint_index_path()), alongside _download_queue.json
and downloads_history.log. It never touches the plain-text log format,
which storage.py keeps identical to the PyQt6 desktop app on purpose.
"""
import hashlib
import json
import os

from settings import get_fingerprint_index_path

_SAMPLE_SIZE = 1024 * 1024  # 1 MB per sample


def compute_quick_fingerprint(path: str) -> dict:
    """Cheap content fingerprint: file size plus a BLAKE2b hash over up
    to three 1 MB samples (start / middle / end - fewer for files
    smaller than that). A same-drive rename or move never touches file
    bytes, so this reliably recognizes "the same file" without reading
    the whole thing - the difference between this and a full hash is
    the difference between a scan that's instant and one that takes
    minutes on a multi-GB video library.

    Returns {"size": int, "mtime": float, "quick_hash": str}. Raises
    OSError if the file can't be read."""
    size = os.path.getsize(path)
    mtime = os.path.getmtime(path)
    h = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as f:
        h.update(f.read(_SAMPLE_SIZE))
        if size > _SAMPLE_SIZE * 2:
            f.seek(size // 2)
            h.update(f.read(_SAMPLE_SIZE))
        if size > _SAMPLE_SIZE:
            f.seek(max(0, size - _SAMPLE_SIZE))
            h.update(f.read(_SAMPLE_SIZE))
    return {"size": size, "mtime": mtime, "quick_hash": h.hexdigest()}


def load_fingerprint_index() -> dict:
    """filename (stem) -> {size, mtime, quick_hash}, for the currently
    active download folder. Missing/corrupt index reads as empty - this
    is a rebuildable cache, never a source of truth on its own."""
    path = get_fingerprint_index_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_fingerprint_index(index: dict) -> None:
    try:
        with open(get_fingerprint_index_path(), "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2)
    except Exception:
        pass


def update_fingerprint(index: dict, filename: str, path: str) -> bool:
    """Computes filename's fingerprint and stores it in index (mutated
    in place) - but skips the actual hash read if the file's size and
    mtime already match what's stored, so re-scanning an unchanged
    library costs nothing beyond a couple of stat() calls per file.
    Returns True if index was actually changed. Safe to call for any
    present file; does nothing (and returns False) if the file can't be
    stat'd."""
    try:
        size = os.path.getsize(path)
        mtime = os.path.getmtime(path)
    except OSError:
        return False

    existing = index.get(filename)
    if existing and existing.get("size") == size and existing.get("mtime") == mtime:
        return False

    try:
        index[filename] = compute_quick_fingerprint(path)
    except OSError:
        return False
    return True


def remove_fingerprint(index: dict, filename: str) -> bool:
    if filename in index:
        del index[filename]
        return True
    return False
