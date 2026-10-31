"""Server-side folder browsing for the folder explorer menus.

The download-folder and target-folder menus in the UI are small file
explorers: they list the sub-folders of a path, drill down level by level,
go up, and create folders. All of that needs the *server's* filesystem
(the browser only sees its own), so this module provides the listing.

Access rules (see list_folder):
  - Requests from the server machine itself may browse anywhere.
  - Requests from other devices (phone, tablet over the LAN) may only browse
    inside that kind's saved folders. Listing arbitrary paths would expose
    the whole disk's folder names to anyone who can reach the port.

Only folders are listed - this is a folder chooser, not a file manager.
"""
import os
import time

MAX_ENTRIES = 2000          # a folder with more sub-folders than this is truncated in the listing
CHILD_PROBE_LIMIT = 300     # sub-folders probed for "has children" (the chevron)
CHILD_PROBE_BUDGET_S = 1.5  # total time allowed for those probes (slow network drives)
INVALID_NAME_CHARS = set('\\/:*?"<>|')


class BrowseError(Exception):
    """A request the browser can't satisfy; `status` maps to the HTTP code."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def is_local_client(host) -> bool:
    return (host or "") in ("127.0.0.1", "::1", "localhost")


def _norm(path: str) -> str:
    return os.path.normpath(os.path.abspath(os.path.expanduser(path)))


def _real(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _within(path: str, root: str) -> bool:
    p, r = _real(path), _real(root)
    return p == r or p.startswith(r.rstrip("\\/") + os.sep)


def _within_any(path: str, roots) -> bool:
    return any(_within(path, r) for r in roots)


def parent_of(path: str):
    """Parent folder, or None at a filesystem/drive root."""
    path = os.path.normpath(path)
    parent = os.path.dirname(path)
    return parent if parent and parent != path else None


def list_drives() -> list:
    """Windows drive letters ('C:\\', ...); on other systems just '/'."""
    if os.name != "nt":
        return [{"name": "/", "path": "/"}]
    import ctypes
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    drives = []
    for i in range(26):
        if mask & (1 << i):
            letter = chr(ord("A") + i)
            drives.append({"name": f"{letter}:", "path": f"{letter}:\\"})
    return drives


def _is_hidden(entry) -> bool:
    if entry.name.startswith("."):
        return True
    if os.name == "nt":
        try:
            # hidden (0x2) or system (0x4): $RECYCLE.BIN, System Volume Information, ...
            return bool(entry.stat().st_file_attributes & 0x6)
        except (OSError, AttributeError):
            return False
    return False


def _has_subfolder(path: str) -> bool:
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    if e.is_dir():
                        return True
                except OSError:
                    continue
    except OSError:
        return False
    return False


def _breadcrumbs(path: str, roots, local: bool) -> list:
    parts = []
    cur = path
    while cur:
        name = os.path.basename(cur) or cur
        parts.append({"name": name, "path": cur})
        cur = parent_of(cur)
    parts.reverse()
    if not local:
        # Remote devices only see the trail from the saved folder they are inside.
        for i, crumb in enumerate(parts):
            if any(_real(crumb["path"]) == _real(r) for r in roots):
                return parts[i:]
        return parts[-1:]
    return parts


def list_folder(path: str, roots, local: bool, show_hidden: bool, last_used: dict, info_only: bool = False) -> dict:
    """Describes `path` for the explorer.

    An empty path is the "home" view: saved folders plus (for the local
    machine) the drives. Otherwise returns the folder's sub-folders, its
    parent (None where the caller must not go higher) and breadcrumbs.
    """
    roots = [r for r in roots if r]

    if not path:
        saved = [
            {"name": os.path.basename(os.path.normpath(r)) or r, "path": os.path.normpath(r), "last_used": last_used.get(os.path.normpath(r), 0)}
            for r in roots if os.path.isdir(r)
        ]
        return {"home": True, "local": local, "path": "", "parent": None, "breadcrumbs": [], "folders": [],
                "saved": saved, "drives": list_drives() if local else []}

    path = _norm(path)
    if not local and not _within_any(path, roots):
        raise BrowseError("That folder is outside your saved folders.", 403)
    if not os.path.isdir(path):
        raise BrowseError("Folder not found.", 404)

    parent = parent_of(path)
    if parent is not None and not local and not _within_any(parent, roots):
        parent = None
    result = {
        "home": False,
        "local": local,
        "path": path,
        "name": os.path.basename(path) or path,
        "parent": parent,
        "at_root": parent is None,
        "breadcrumbs": _breadcrumbs(path, roots, local),
        "writable": os.access(path, os.W_OK),
    }
    if info_only:
        return result

    folders = []
    truncated = False
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if not entry.is_dir():
                        continue
                    if not show_hidden and _is_hidden(entry):
                        continue
                except OSError:
                    continue
                if len(folders) >= MAX_ENTRIES:
                    truncated = True
                    break
                full = os.path.normpath(entry.path)
                folders.append({"name": entry.name, "path": full, "last_used": last_used.get(full, 0), "link": entry.is_symlink()})
    except PermissionError:
        raise BrowseError("Permission denied.", 403)
    except OSError as e:
        raise BrowseError(str(e), 400)

    folders.sort(key=lambda f: f["name"].lower())
    deadline = time.monotonic() + CHILD_PROBE_BUDGET_S
    for i, folder in enumerate(folders):
        if i >= CHILD_PROBE_LIMIT or time.monotonic() > deadline:
            folder["has_children"] = None      # unknown: the UI shows the chevron and finds out on entry
            continue
        folder["has_children"] = _has_subfolder(folder["path"])

    result["folders"] = folders
    result["truncated"] = truncated
    return result


def validate_new_folder_name(name: str) -> str:
    name = (name or "").strip()
    if not name or name in (".", ".."):
        raise BrowseError("Enter a folder name.")
    if len(name) > 255:
        raise BrowseError("That name is too long.")
    if any(c in INVALID_NAME_CHARS for c in name) or any(ord(c) < 32 for c in name):
        raise BrowseError('A folder name can\'t contain \\ / : * ? " < > |')
    if name.endswith((".", " ")):
        raise BrowseError("A folder name can't end with a dot or a space.")
    return name


def make_folder(parent: str, name: str, roots, local: bool) -> str:
    """Creates `name` inside `parent` and returns the new folder's path."""
    name = validate_new_folder_name(name)
    parent = _norm(parent)
    if not local and not _within_any(parent, roots):
        raise BrowseError("That folder is outside your saved folders.", 403)
    if not os.path.isdir(parent):
        raise BrowseError("The parent folder no longer exists.", 404)
    new_path = os.path.join(parent, name)
    if os.path.exists(new_path):
        raise BrowseError("A file or folder with that name already exists.", 409)
    try:
        os.mkdir(new_path)
    except PermissionError:
        raise BrowseError("Permission denied.", 403)
    except OSError as e:
        raise BrowseError(str(e), 400)
    return new_path
