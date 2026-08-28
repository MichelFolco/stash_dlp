"""Live filesystem scan that turns a single saved "root" folder into a
nested tree of its subfolders, for the DL:/Target: quick-select dropdown.
Nothing here is persisted - it's recomputed from disk every time the
dropdown opens, so it's always accurate and there's no cache to
invalidate. Ordering (most-recently-used first) comes from the
dir_last_used map in settings.py, which the caller passes in.

MAX_DEPTH is a safety net, not a real-world limit - it exists so
accidentally adding a drive root or a folder with a deep/looping
structure can't produce a runaway scan or an unusable menu, not
because 5 levels of nesting is expected to matter for a real video
library.
"""
import os

MAX_DEPTH = 5

# Skip these regardless of the hidden-name check below - noise that
# shows up under real folders on Windows/network drives and is never
# something a person wants to pick as a download destination.
_SKIP_NAMES = {"$RECYCLE.BIN", "System Volume Information"}


def _is_hidden(name: str) -> bool:
    return name.startswith(".") or name in _SKIP_NAMES


def _scan_level(path: str, depth: int, last_used: dict) -> list:
    """Returns the child nodes directly under `path`, sorted
    most-recently-used first (unused folders fall back to alphabetical,
    after every used one). Recurses into each child up to MAX_DEPTH."""
    entries = []
    try:
        with os.scandir(path) as it:
            for entry in it:
                if not entry.name or _is_hidden(entry.name):
                    continue
                try:
                    # is_symlink() first - a symlink to a directory would
                    # otherwise also pass is_dir(), and following it is
                    # exactly the loop risk we want to skip entirely.
                    if entry.is_symlink() or not entry.is_dir():
                        continue
                except OSError:
                    continue
                entries.append(entry.path)
    except (PermissionError, FileNotFoundError, OSError):
        return []

    def sort_key(p):
        ts = last_used.get(p, 0)
        return (-ts if ts else 0, ts == 0, os.path.basename(p).lower())

    entries.sort(key=sort_key)

    nodes = []
    for child_path in entries:
        child_path = os.path.normpath(child_path)
        node = {
            "path": child_path,
            "name": os.path.basename(child_path),
            "last_used": last_used.get(child_path, 0),
        }
        if depth < MAX_DEPTH:
            node["children"] = _scan_level(child_path, depth + 1, last_used)
        else:
            node["children"] = []
        nodes.append(node)
    return nodes


def scan_root_tree(root_path: str, last_used: dict) -> dict:
    """Returns the root node itself (path/name/last_used) plus its
    nested `children` tree, live-scanned from disk. Callers should
    already have confirmed root_path exists - a missing root is a
    settings.py concern (stale-root cleanup), not this module's."""
    root_path = os.path.normpath(root_path)
    return {
        "path": root_path,
        "name": os.path.basename(root_path) or root_path,
        "last_used": last_used.get(root_path, 0),
        "children": _scan_level(root_path, 1, last_used),
    }
