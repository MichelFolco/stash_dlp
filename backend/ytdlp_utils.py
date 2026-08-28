"""Async wrappers around the yt-dlp CLI. These replace the QProcess-based
TitleFetcher / DownloadWorker.run() / boot_filesystem_scan update-check
logic from the desktop app, using asyncio subprocesses instead of Qt's
event loop.
"""
import asyncio
import json
import os
import re
import sys
from urllib.parse import urlparse

from config import AUDIO_EXTENSIONS
from procflags import NO_CONSOLE_KWARGS
from settings import get_save_dir, get_converted_dir, get_ytdlp_args


def is_audio_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in AUDIO_EXTENSIONS


def clean_filename(raw: str) -> str:
    s = re.sub(r'[\\/:*?"<>|]', "-", raw)
    return re.sub(r"\s+", " ", s).strip()


def split_args_string(raw_args: str) -> list:
    """Splits an external program's command-line-args string into a
    token list for subprocess.Popen, respecting "quoted sections" (so a
    single argument can contain spaces) but NOT treating backslashes as
    escape characters - unlike shlex.split, which would mangle Windows
    paths like C:\\Program Files\\foo.exe that show up inside a {file}
    substitution or a quoted arg."""
    tokens = []
    current = []
    in_quotes = False
    for ch in raw_args or "":
        if ch == '"':
            in_quotes = not in_quotes
            continue
        if ch.isspace() and not in_quotes:
            if current:
                tokens.append("".join(current))
                current = []
            continue
        current.append(ch)
    if current:
        tokens.append("".join(current))
    return tokens


def build_open_with_command(program_path: str, args_template: str, media_path: str) -> list:
    """Builds the argv list for launching an external program against a
    completed download. If the args template contains a {file} token,
    it's substituted in place (letting the user control ordering
    relative to other flags); otherwise the media path is appended as
    the final argument."""
    tokens = split_args_string(args_template)
    if any("{file}" in t for t in tokens):
        tokens = [t.replace("{file}", media_path) for t in tokens]
        return [program_path] + tokens
    return [program_path] + tokens + [media_path]


def get_domain(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
        host = re.sub(r"^(www\d?|m)\.", "", host)
        return host.split(".")[0]
    except Exception:
        return ""


def format_file_size(num_bytes) -> str:
    try:
        size = float(num_bytes)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


def find_media_file(filename: str, save_dir: str | None = None):
    """Returns the full path to the actual downloaded file matching this
    job's filename stem (preferring .mp4), or None if nothing's there.

    save_dir can be supplied for jobs whose destination was captured when
    they were queued; otherwise the current configured download folder is used."""
    save_dir = save_dir or get_save_dir()
    mp4_path = os.path.join(save_dir, filename + ".mp4")
    if os.path.isfile(mp4_path):
        return mp4_path
    try:
        for fname in os.listdir(save_dir):
            full_path = os.path.join(save_dir, fname)
            if os.path.isfile(full_path) and os.path.splitext(fname)[0] == filename:
                return full_path
    except OSError:
        pass
    return None


def find_converted_file(filename: str):
    """Returns the full path to a re-encoded twin of this job's file in
    the Converted/ subfolder, or None if there isn't one. Matches on the
    exact filename stem first (the common case, since the encode
    manager reuses the source stem); if that's not there, falls back to
    the newest file in Converted/ whose stem starts with the source
    stem followed by ' (' - covering the ' (2)', ' (3)', ... suffixes
    _unique_output_path() appends when a name collision occurs."""
    converted_dir = get_converted_dir()
    try:
        entries = os.listdir(converted_dir)
    except OSError:
        return None

    for fname in entries:
        full_path = os.path.join(converted_dir, fname)
        if os.path.isfile(full_path) and os.path.splitext(fname)[0] == filename:
            return full_path

    candidates = []
    suffix_prefix = filename + " ("
    for fname in entries:
        full_path = os.path.join(converted_dir, fname)
        if not os.path.isfile(full_path):
            continue
        stem = os.path.splitext(fname)[0]
        if stem.startswith(suffix_prefix):
            candidates.append(full_path)
    if candidates:
        candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return candidates[0]

    return None


def list_converted_stems() -> set:
    """Returns the set of file stems currently sitting in Converted/,
    listing the folder exactly once. Lets a caller that needs a yes/no
    twin-exists answer for many filenames (e.g. job_manager's per-refresh
    has_twin check) do it against one directory listing instead of
    calling find_converted_file() - and paying its own os.listdir() -
    once per filename."""
    converted_dir = get_converted_dir()
    try:
        entries = os.listdir(converted_dir)
    except OSError:
        return set()
    stems = set()
    for fname in entries:
        if os.path.isfile(os.path.join(converted_dir, fname)):
            stems.add(os.path.splitext(fname)[0])
    return stems


def has_converted_twin(filename: str, converted_stems: set) -> bool:
    """True if `filename` has a twin in Converted/, given the stem set
    from list_converted_stems(). Mirrors find_converted_file()'s
    exact-stem-first, then ' (' suffix fallback matching, but purely
    in-memory - no disk access."""
    if filename in converted_stems:
        return True
    suffix_prefix = filename + " ("
    return any(stem.startswith(suffix_prefix) for stem in converted_stems)


def get_downloaded_file_size(filename: str, save_dir: str | None = None):
    path = find_media_file(filename, save_dir)
    if path is None:
        return None
    try:
        return os.path.getsize(path)
    except OSError:
        return None


async def fetch_title(url: str) -> str:
    """Mirrors TitleFetcher.run()."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp",
            "--get-title",
            "--no-warnings",
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **NO_CONSOLE_KWARGS,
        )
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
        stdout = stdout_bytes.decode("utf-8", errors="ignore")
        title = stdout.strip().splitlines()[0] if stdout.strip() else "Unknown Title"
        return title
    except Exception:
        return "Unknown Title"


async def probe_playlist(url: str) -> dict:
    """Cheaply checks whether `url` is a playlist/channel/multi-video page
    rather than a single video, using yt-dlp's --flat-playlist mode (lists
    entries without resolving/downloading each one, so this stays fast
    even against a large channel). Called on every pasted URL - single
    video pastes are the common case, so any failure here just falls
    back to treating it as one.

    Returns {'is_playlist': bool, 'playlist_title': str,
    'entries': [{'url': str, 'title': str}, ...]}. A result of exactly
    one entry is treated as NOT a playlist (single-video pages some
    extractors still wrap in a one-item 'entries' list), so the normal
    single-download pipeline handles it instead of the playlist batch
    path. Never raises - any error/timeout just yields is_playlist=False."""
    ytdlp_args = get_ytdlp_args()
    extra_args = split_args_string(ytdlp_args["default_args"])
    extra_args += split_args_string(ytdlp_args["domain_args"].get(get_domain(url), ""))

    try:
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp",
            "--flat-playlist",
            "--dump-single-json",
            "--no-warnings",
            "--ignore-errors",
            *extra_args,
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            **NO_CONSOLE_KWARGS,
        )
    except Exception:
        return {"is_playlist": False, "playlist_title": "", "entries": []}

    try:
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return {"is_playlist": False, "playlist_title": "", "entries": []}
    except Exception:
        return {"is_playlist": False, "playlist_title": "", "entries": []}

    try:
        data = json.loads(stdout_bytes.decode("utf-8", errors="ignore"))
    except Exception:
        return {"is_playlist": False, "playlist_title": "", "entries": []}

    raw_entries = data.get("entries")
    if not isinstance(raw_entries, list) or len(raw_entries) < 2:
        return {"is_playlist": False, "playlist_title": "", "entries": []}

    entries = []
    for entry in raw_entries:
        if not entry:
            continue
        entry_url = entry.get("url") or entry.get("webpage_url") or entry.get("original_url") or ""
        # Some extractors/flat-playlist versions put a bare video ID in
        # "url" rather than a resolvable link - fall back to
        # webpage_url when what we have doesn't look like an actual URL.
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", entry_url):
            entry_url = entry.get("webpage_url") or ""
        if not entry_url:
            continue
        title = entry.get("title") or entry.get("id") or "Untitled"
        entries.append({"url": entry_url, "title": title})

    if len(entries) < 2:
        return {"is_playlist": False, "playlist_title": "", "entries": []}

    return {
        "is_playlist": True,
        "playlist_title": data.get("title") or "",
        "entries": entries,
    }


async def check_and_update_ytdlp():
    """Mirrors boot_filesystem_scan/finalize_boot_scan's version + update
    detection. Returns (version, just_updated).

    Uses the nightly channel instead of stable, since nightly picks up
    extractor fixes faster than the ~weekly stable releases. There are
    two totally different update mechanisms depending on how yt-dlp was
    installed, and only one works for a given install:

    - Standalone binary builds: `yt-dlp --update-to nightly` works and
      self-replaces the executable.
    - pip installs: `--update-to` is a no-op that just prints
      "ERROR: You installed yt-dlp with pip or using the wheel from
      PyPi; Use that to update" and exits - it can NEVER update a pip
      install, regardless of channel. Pip installs have to instead be
      upgraded via `pip install -U --pre "yt-dlp[default]"` (--pre is
      what pulls in nightly/dev builds from PyPI, since pip ignores
      pre-release versions by default).

    We try --update-to first (cheap, and correct for binary installs).
    If its output shows the "installed with pip" error, we fall back to
    the pip upgrade path using sys.executable so it targets the exact
    Python environment this app is running under."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp",
            "--update-to",
            "nightly",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **NO_CONSOLE_KWARGS,
        )
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout_bytes.decode("utf-8", errors="ignore")
    except Exception:
        output = ""

    if re.search(r"installed yt-dlp with pip", output, re.IGNORECASE):
        return await _update_ytdlp_via_pip()

    # Nightly output looks like:
    #   "Updated yt-dlp to nightly@2025.07.25.233059 from yt-dlp/yt-dlp-nightly-builds"
    #   "yt-dlp is up to date (nightly@2025.07.25.233059 from yt-dlp/yt-dlp-nightly-builds)"
    updated_match = re.search(r"Updated yt-dlp to\s+(\S+)", output, re.IGNORECASE)
    uptodate_match = re.search(r"up to date\s*\(\s*(\S+)", output, re.IGNORECASE)

    if updated_match:
        return updated_match.group(1), True

    if uptodate_match:
        return uptodate_match.group(1), False

    return await fetch_version_sync(), False


async def _update_ytdlp_via_pip():
    """Upgrades a pip-installed yt-dlp to the latest nightly/dev build
    on PyPI. Returns (version, just_updated), matching
    check_and_update_ytdlp()'s contract."""
    before = await fetch_version_sync()

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m", "pip", "install", "-U", "--pre", "yt-dlp[default]",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **NO_CONSOLE_KWARGS,
        )
        await asyncio.wait_for(proc.communicate(), timeout=60)
    except Exception:
        pass

    after = await fetch_version_sync()
    return after, (after != before and after != "")


async def fetch_version_sync() -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp",
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **NO_CONSOLE_KWARGS,
        )
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        out = stdout_bytes.decode("utf-8", errors="ignore").strip()
        return out.splitlines()[0].strip() if out else ""
    except Exception:
        return ""
