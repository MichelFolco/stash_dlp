import asyncio
import mimetypes
import os
import subprocess
import sys
from typing import Optional, List
from urllib.parse import quote

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import PROJECT_ROOT, BUNDLE_DIR, FROZEN, HOST, PORT, APP_VERSION, CONVERTED_DIR_NAME
from diskspace import get_free_space_label
from encode_manager import EncodeManager
from filesystem_scan import scan_filesystem
from folder_dialog import ask_directory, ask_file_path
from folder_tree import scan_root_tree
from job_manager import JobManager, ConnectionManager, NeedsDecisionError
from m3u8_finder import find_m3u8, M3u8NotFound
from procflags import NO_CONSOLE_KWARGS
from settings import (
    get_save_dir, set_save_dir, get_save_dir_roots, add_save_dir_root, remove_save_dir_root,
    get_target_dir, set_target_dir, get_target_dir_roots, add_target_dir_root, remove_target_dir_root,
    get_dir_last_used_map,
    get_external_programs, get_external_program, add_external_program,
    update_external_program, delete_external_program,
    get_converted_dir, get_download_prefs, set_download_prefs,
    get_sync_clip_duration, set_sync_clip_duration,
    get_ytdlp_args, set_ytdlp_default_args, set_ytdlp_domain_args, delete_ytdlp_domain_args,
    get_recent_stash_tags, push_recent_stash_tag,
)
from storage import search_history, get_history_entries, delete_history_entry, lookup_history_in_folder, HistoryLookupError
from thumbnails import get_thumbnail_path
import stash_integration
import audio_sync
from clipboard_monitor import monitor_clipboard
from ytdlp_utils import (
    clean_filename, fetch_title, get_domain, check_and_update_ytdlp, find_media_file,
    find_converted_file, build_open_with_command, format_file_size, probe_playlist,
)

STREAM_CHUNK_SIZE = 1024 * 1024  # 1 MB

app = FastAPI(title="Stash DLP Web")

# Allows browser-side callers on a different origin (e.g. the Stash web UI,
# which runs on its own host/port) to call this API directly - needed for
# the Stash "history lookup" plugin. Wide open since this is a LAN/Tailscale
# tool with no auth of its own; tighten allow_origins if that ever changes.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

STATIC_DIR = os.path.join(str(BUNDLE_DIR), "static")

# Shared across both managers so downloads and encode jobs broadcast over
# the SAME websocket connection - the frontend only has to manage one
# socket rather than juggling two.
connections = ConnectionManager()
job_manager = JobManager(connections=connections)
encode_manager = EncodeManager(connections=connections)


def _has_reencode_twin(filename: str) -> bool:
    """True if a completed Encode Manager job's source traces back to
    this download - mirrors the frontend's isReencoded(). Used to keep
    re-encode and audio-sync mutually exclusive, since both would want
    the same Converted/<stem> slot."""
    for encode_job in encode_manager.jobs.values():
        if encode_job.get("status") != "DONE":
            continue
        source_stem = os.path.splitext(encode_job.get("source_filename") or "")[0]
        if source_stem == filename:
            return True
    return False

# yt-dlp version state, populated on startup (mirrors ytdlp_version /
# ytdlp_just_updated on the desktop app's main window)
version_state = {"version": "", "just_updated": False}


@app.on_event("startup")
async def on_startup():
    version, just_updated = await check_and_update_ytdlp()
    version_state["version"] = version
    version_state["just_updated"] = just_updated

    done_jobs = scan_filesystem()
    await job_manager.seed_from_filesystem(done_jobs)

    encode_manager.start()
    asyncio.create_task(monitor_clipboard(connections))


# ── Request/response models ────────────────────────────────────
class StartJobRequest(BaseModel):
    url: str
    filename: str
    res_cap: str = "720p"
    original_pasted_url: str = ""


class FetchTitleRequest(BaseModel):
    url: str
    tag_domain: bool = True


class FindLinkRequest(BaseModel):
    url: str
    tag_domain: bool = True


class HistorySearchRequest(BaseModel):
    query: str


class HistoryDeleteRequest(BaseModel):
    timestamp: str
    filename: str
    url: str


class HistoryLookupRequest(BaseModel):
    folder: str
    filename: str


class CancelRequest(BaseModel):
    filename: str


class BatchFilenamesRequest(BaseModel):
    filenames: List[str]


class MoveToTargetRequest(BaseModel):
    filename: str
    variant: Optional[str] = None  # "original" | "reencoded" | None


class SaveDirRequest(BaseModel):
    save_dir: str


class TargetDirRequest(BaseModel):
    target_dir: str


class RootFolderRequest(BaseModel):
    path: str


class DownloadPrefsRequest(BaseModel):
    quality: str
    tag_domain: bool
    m3u_sniffer: bool
    auto_m3u_retry: bool = True
    auto_confirm_titles: bool = False
    clipboard_monitor: bool = False
    title_prefix: str = ""
    title_prefix_enabled: bool = False


class PlaylistProbeRequest(BaseModel):
    url: str
    tag_domain: bool = True


class PlaylistQueueEntry(BaseModel):
    url: str
    title: str


class PlaylistQueueRequest(BaseModel):
    entries: List[PlaylistQueueEntry]
    res_cap: str = "720p"
    number_titles: bool = False


class YtdlpDefaultArgsRequest(BaseModel):
    args: str


class YtdlpDomainArgsRequest(BaseModel):
    domain: str
    args: str = ""


class YtdlpDomainArgsDeleteRequest(BaseModel):
    domain: str


class RenameRequest(BaseModel):
    filename: str
    new_filename: str


class PlaybackPositionRequest(BaseModel):
    filename: str
    position: float
    completed: bool = False


class ExternalProgramRequest(BaseModel):
    name: str
    path: str
    args: str = ""


class ExternalProgramUpdateRequest(BaseModel):
    id: str
    name: str
    path: str
    args: str = ""


class ExternalProgramDeleteRequest(BaseModel):
    id: str


class OpenWithRequest(BaseModel):
    filename: str
    program_id: str


class EncodeOptionsRequest(BaseModel):
    mode: str = "crf"                       # "crf" | "size"
    codec: str = "h265"
    encoder_backend: str = "software"       # "software" | "amf" | "nvenc" | "qsv"
    crf: int = 22
    preset: str = "medium"
    target_size_mb: Optional[float] = None
    resolution_cap: str = "source"
    force_ar: bool = False
    force_ar_label: str = ""
    force_ar_width: Optional[int] = None
    force_ar_height: Optional[int] = None
    deinterlace: bool = False
    auto_crop: bool = False
    denoise: bool = False
    audio_mode: str = "copy"
    subtitles_mode: str = "copy"
    container: str = "mp4"
    oversized_behavior: str = "flag"        # "flag" | "discard"


class EncodeSourceRequest(BaseModel):
    filename: str          # a ledger entry, resolved via find_media_file


class EncodeEstimateRequest(BaseModel):
    filename: str
    options: EncodeOptionsRequest


class EnqueueEncodeRequest(BaseModel):
    filename: str
    options: EncodeOptionsRequest


class EncodeJobIdRequest(BaseModel):
    job_id: str


class EncodeDeleteRequest(BaseModel):
    job_id: str
    delete_output: bool = False


class StashImportRequest(BaseModel):
    scene: str
    tag_id: Optional[str] = None    # set when importing from a Check Tag result
    tag_name: Optional[str] = None


class StashTagCheckRequest(BaseModel):
    tag: str


class ReplaceSourceRequest(BaseModel):
    filename: str
    variant: Optional[str] = None  # "original" | "reencoded" | None
    delete_tag_ids: List[str] = []  # any subset of job["stash_tags"] to remove from the scene


class SyncAudioApplyRequest(BaseModel):
    filename: str
    delay_ms: float


class SyncAudioConfirmRequest(BaseModel):
    filename: str
    delay_ms: float


class SyncClipCreateRequest(BaseModel):
    filename: str
    start_seconds: float
    delay_ms: float = 0.0


# ── REST endpoints ──────────────────────────────────────────────
@app.get("/api/app_version")
async def get_app_version():
    """stash_dlp's own version (see APP_VERSION in config.py), shown to
    the user when they click the logo - distinct from /api/version below,
    which reports the bundled yt-dlp version instead."""
    return {"version": APP_VERSION}


@app.get("/api/version")
async def get_version():
    return version_state


@app.post("/api/version/check")
async def api_check_ytdlp_update():
    """Manually re-runs the same yt-dlp --update-to nightly check done at
    startup, so the user doesn't have to restart the whole app just to
    pick up a newer yt-dlp build (sites break yt-dlp often enough that
    this is worth a one-click action rather than only checking once per
    boot)."""
    version, just_updated = await check_and_update_ytdlp()
    version_state["version"] = version
    version_state["just_updated"] = just_updated
    return version_state


@app.get("/api/settings")
async def api_get_settings():
    save_dir = get_save_dir()
    return {
        "save_dir": save_dir,
        "roots": get_save_dir_roots(),
        "free_space": get_free_space_label(save_dir),
    }


@app.post("/api/settings")
async def api_set_settings(req: SaveDirRequest):
    try:
        new_path = set_save_dir(req.save_dir)
    except (ValueError, OSError) as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    # The folder changed, so re-scan it and refresh every connected client
    done_jobs = scan_filesystem()
    await job_manager.seed_from_filesystem(done_jobs, replace=True)
    snapshot = job_manager.snapshot()
    await job_manager.connections.broadcast({"type": "refresh", "jobs": snapshot})
    return {
        "save_dir": new_path,
        "roots": get_save_dir_roots(),
        "free_space": get_free_space_label(new_path),
        "jobs": snapshot,
    }


@app.post("/api/settings/roots/add")
async def api_add_save_dir_root(req: RootFolderRequest):
    """Adds a new saved root download folder (modal's 'Set Folder' flow
    already goes through /api/settings above, since setting a folder
    auto-adds it as a root - this endpoint is for explicitly adding a
    root without necessarily also making it the active download
    folder). Subfolder-of-an-existing-root paths are rejected; adding a
    folder that's an ancestor of existing roots absorbs them."""
    try:
        roots = add_save_dir_root(req.path)
    except (ValueError, OSError) as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    return {"roots": roots}


@app.post("/api/settings/roots/remove")
async def api_remove_save_dir_root(req: RootFolderRequest):
    return {"roots": remove_save_dir_root(req.path)}


@app.get("/api/settings/roots/tree")
async def api_get_save_dir_tree():
    """Live-scans every saved download-folder root and returns each as
    a nested subfolder tree (most-recently-used first at every level),
    for the DL: quick-select dropdown."""
    last_used = get_dir_last_used_map()
    roots = get_save_dir_roots()  # already most-recently-used first
    return {"roots": [scan_root_tree(r, last_used) for r in roots]}


@app.get("/api/download-prefs")
async def api_get_download_prefs():
    return get_download_prefs()


@app.post("/api/download-prefs")
async def api_set_download_prefs(req: DownloadPrefsRequest):
    return set_download_prefs(
        req.quality, req.tag_domain, req.m3u_sniffer, req.auto_m3u_retry, req.auto_confirm_titles,
        req.clipboard_monitor, req.title_prefix, req.title_prefix_enabled,
    )


def _apply_title_prefix(title: str) -> str:
    """Prepends the saved title prefix, if the toggle is on and there's
    actually a prefix to add. Applied at the same three spots tag_domain
    already is (fetch-title, find-link, playlist probe), so a prefix
    shows up consistently everywhere a title gets generated, not just
    the single-download review box."""
    prefs = get_download_prefs()
    if prefs.get("title_prefix_enabled") and prefs.get("title_prefix"):
        return prefs["title_prefix"] + title
    return title


@app.get("/api/ytdlp-args")
async def api_get_ytdlp_args():
    return get_ytdlp_args()


@app.get("/api/ytdlp-args/for-url")
async def api_get_ytdlp_args_for_url(url: str):
    """Resolves the domain for a pasted URL server-side (reusing the
    same get_domain() the file-tagging feature already uses, so the
    frontend never has to duplicate that hostname-parsing logic) and
    reports back whether a saved rule exists for it - lets the input
    field's args indicator light up without the frontend needing its
    own copy of the domain/args data."""
    domain = get_domain(url)
    args = get_ytdlp_args()
    return {
        "domain": domain,
        "args": args["domain_args"].get(domain, ""),
    }


@app.post("/api/ytdlp-args/default")
async def api_set_ytdlp_default_args(req: YtdlpDefaultArgsRequest):
    return set_ytdlp_default_args(req.args)


@app.post("/api/ytdlp-args/domain")
async def api_set_ytdlp_domain_args(req: YtdlpDomainArgsRequest):
    try:
        return set_ytdlp_domain_args(req.domain, req.args)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})


@app.post("/api/ytdlp-args/domain/delete")
async def api_delete_ytdlp_domain_args(req: YtdlpDomainArgsDeleteRequest):
    return delete_ytdlp_domain_args(req.domain)


@app.post("/api/target-settings/roots/add")
async def api_add_target_dir_root(req: RootFolderRequest):
    try:
        roots = add_target_dir_root(req.path)
    except (ValueError, OSError) as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    return {"roots": roots}


@app.post("/api/target-settings/roots/remove")
async def api_remove_target_dir_root(req: RootFolderRequest):
    return {"roots": remove_target_dir_root(req.path)}


@app.get("/api/target-settings/roots/tree")
async def api_get_target_dir_tree():
    """Live-scans every saved target-folder root - see
    api_get_save_dir_tree() above for the shape and reasoning."""
    last_used = get_dir_last_used_map()
    roots = get_target_dir_roots()  # already most-recently-used first
    return {"roots": [scan_root_tree(r, last_used) for r in roots]}


@app.post("/api/browse-folder")
async def api_browse_folder(request: Request):
    """Pops a native folder-picker on the server machine. Gated to
    localhost requests, since a dialog on your PC is useless if you're
    browsing in from your phone over Tailscale."""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse(
            status_code=400,
            content={"error": "Browse only works when the browser is on the same machine as the server."},
        )
    try:
        path = await ask_directory(get_save_dir())
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    return {"path": path}


@app.get("/api/external-programs")
async def api_get_external_programs():
    # Read-only listing - harmless to expose to any connected client,
    # same as save_dir/target_dir already are via /api/settings. Only
    # the mutating endpoints below (and the launch endpoint) are
    # localhost-gated.
    return {"programs": get_external_programs()}


@app.post("/api/external-programs")
async def api_add_external_program(req: ExternalProgramRequest, request: Request):
    """Adds a new external program. Gated to localhost - see
    api_browse_folder for why managing these only makes sense on the
    machine that actually has them installed."""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse(
            status_code=400,
            content={"error": "Managing external programs only works when the browser is on the same machine as the server."},
        )
    try:
        programs = add_external_program(req.name, req.path, req.args)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    return {"programs": programs}


@app.post("/api/external-programs/update")
async def api_update_external_program(req: ExternalProgramUpdateRequest, request: Request):
    """Same localhost gating as api_add_external_program."""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse(
            status_code=400,
            content={"error": "Managing external programs only works when the browser is on the same machine as the server."},
        )
    try:
        programs = update_external_program(req.id, req.name, req.path, req.args)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    return {"programs": programs}


@app.post("/api/external-programs/delete")
async def api_delete_external_program(req: ExternalProgramDeleteRequest, request: Request):
    """Same localhost gating as api_add_external_program."""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse(
            status_code=400,
            content={"error": "Managing external programs only works when the browser is on the same machine as the server."},
        )
    programs = delete_external_program(req.id)
    return {"programs": programs}


@app.post("/api/browse-program-file")
async def api_browse_program_file(request: Request):
    """Native file-picker for choosing an external program's executable.
    Same localhost gating as api_browse_folder - see that endpoint for
    why."""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse(
            status_code=400,
            content={"error": "Browse only works when the browser is on the same machine as the server."},
        )
    try:
        path = await ask_file_path(get_save_dir())
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    return {"path": path}


@app.get("/api/target-settings")
async def api_get_target_settings():
    target_dir = get_target_dir()
    return {
        "target_dir": target_dir,
        "roots": get_target_dir_roots(),
        "free_space": get_free_space_label(target_dir) if target_dir else "",
    }


@app.post("/api/target-settings")
async def api_set_target_settings(req: TargetDirRequest):
    try:
        new_path = set_target_dir(req.target_dir)
    except (ValueError, OSError) as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    return {
        "target_dir": new_path,
        "roots": get_target_dir_roots(),
        "free_space": get_free_space_label(new_path),
    }


@app.post("/api/browse-target-folder")
async def api_browse_target_folder(request: Request):
    """Same localhost gating as /api/browse-folder - see that endpoint
    for why."""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse(
            status_code=400,
            content={"error": "Browse only works when the browser is on the same machine as the server."},
        )
    try:
        path = await ask_directory(get_target_dir())
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    return {"path": path}


@app.get("/api/stash/status")
async def api_stash_status():
    """Cheap liveness check the frontend polls to decide whether to show
    the Stash button at all."""
    running = await stash_integration.is_stash_running()
    return {"running": running}


@app.post("/api/import/stash")
async def api_import_stash(req: StashImportRequest):
    """Import the media file belonging to a Stash scene into the current
    Stash DLP download folder. Only the scene's file path is requested;
    no Stash metadata is imported, except that when this import came from
    a Check Tag result, that one tag's id/name are recorded on the job so
    the UI can show a pill for it (see StashImportRequest.tag_id/tag_name)."""
    try:
        result = await stash_integration.import_stash_scene(
            job_manager, req.scene, tag_id=req.tag_id, tag_name=req.tag_name
        )
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except RuntimeError as e:
        return JSONResponse(status_code=502, content={"error": str(e)})

    return {"ok": True, "job": result}


@app.get("/api/stash/recent-tags")
async def api_stash_recent_tags():
    """Rolling list of the last few tags searched via Check Tag, for the
    quick-pick chips shown when the Check Tag modal is opened."""
    return {"recent_tags": get_recent_stash_tags()}


@app.post("/api/stash/check-tag")
async def api_stash_check_tag(req: StashTagCheckRequest):
    """Look up a Stash tag by name and list every scene that has it."""
    try:
        result = await stash_integration.find_scenes_by_tag(req.tag)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except RuntimeError as e:
        return JSONResponse(status_code=502, content={"error": str(e)})
    recent_tags = push_recent_stash_tag(result.get("tag_name", ""))
    return {"ok": True, **result, "recent_tags": recent_tags}


@app.get("/api/stash/largest-files")
async def api_stash_largest_files():
    """Return the 50 largest scene files in the Stash library."""
    try:
        result = await stash_integration.find_largest_scenes(limit=50)
    except RuntimeError as e:
        return JSONResponse(status_code=502, content={"error": str(e)})
    return {"ok": True, **result}


@app.post("/api/jobs/replace-source")
async def api_replace_source(req: ReplaceSourceRequest):
    try:
        tag_result = await stash_integration.replace_source(
            job_manager, req.filename, variant=req.variant, delete_tag_ids=req.delete_tag_ids
        )
    except NeedsDecisionError as e:
        return JSONResponse(status_code=409, content={"needs_decision": True, **e.info})
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:
        # Surfaces anything unanticipated (e.g. a genuinely permanent file
        # lock the retry loop in replace_source gave up on) as a real
        # error message instead of an opaque 500 the frontend can only
        # report as "couldn't reach the server".
        return JSONResponse(status_code=400, content={"error": f"Unexpected error: {e}"})
    await job_manager.connections.broadcast({"type": "job_deleted", "filename": req.filename})
    return {"ok": True, **tag_result}


@app.post("/api/jobs/replace-with-twin")
async def api_replace_with_twin(req: CancelRequest):
    try:
        await job_manager.replace_with_twin(req.filename)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    return {"ok": True, "jobs": job_manager.snapshot()}


@app.post("/api/jobs/move-to-target")
async def api_move_to_target(req: MoveToTargetRequest):
    try:
        await job_manager.move_to_target(req.filename, variant=req.variant)
    except NeedsDecisionError as e:
        return JSONResponse(status_code=409, content={"needs_decision": True, **e.info})
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    await job_manager.connections.broadcast({"type": "job_deleted", "filename": req.filename})
    return {"ok": True}


@app.post("/api/jobs/move-all-to-target")
async def api_move_all_to_target():
    try:
        result = await job_manager.move_all_to_target()
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    snapshot = job_manager.snapshot()
    await job_manager.connections.broadcast({"type": "refresh", "jobs": snapshot})
    return {
        "moved": result["moved"],
        "failed": result["failed"],
        "pending_decisions": result["pending_decisions"],
        "jobs": snapshot,
    }


@app.post("/api/jobs/move-selected-to-target")
async def api_move_selected_to_target(req: BatchFilenamesRequest):
    """Multi-select version of move-to-target: moves only the given
    filenames rather than every completed item. Same pending_decisions
    contract as move-all-to-target for files with a re-encoded twin."""
    try:
        result = await job_manager.move_selected_to_target(req.filenames)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    snapshot = job_manager.snapshot()
    await job_manager.connections.broadcast({"type": "refresh", "jobs": snapshot})
    return {
        "moved": result["moved"],
        "failed": result["failed"],
        "pending_decisions": result["pending_decisions"],
        "jobs": snapshot,
    }


@app.get("/api/jobs")
async def get_jobs():
    return job_manager.snapshot()


@app.post("/api/fetch-title")
async def api_fetch_title(req: FetchTitleRequest):
    title = await fetch_title(req.url)
    tag = ""
    if req.tag_domain:
        domain = get_domain(req.url)
        if domain:
            tag = f" [{domain}]"
    return {"title": _apply_title_prefix(clean_filename(title) + tag), "raw_title": title}


@app.post("/api/find-link")
async def api_find_link(req: FindLinkRequest):
    try:
        stream_url, page_title = await find_m3u8(req.url)
    except M3u8NotFound as e:
        return JSONResponse(status_code=422, content={"error": str(e)})

    tag = ""
    if req.tag_domain:
        domain = get_domain(req.url)
        if domain:
            tag = f" [{domain}]"

    if not page_title:
        from datetime import datetime
        page_title = f"M3U8_Stream_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    return {
        "stream_url": stream_url,
        "suggested_title": _apply_title_prefix(clean_filename(page_title) + tag),
    }


@app.post("/api/playlist/probe")
async def api_probe_playlist(req: PlaylistProbeRequest):
    """Cheap yt-dlp --flat-playlist check run against every pasted URL,
    before the normal single-video fetch-title/M3U-sniff pipeline - if
    this comes back is_playlist=False (the common case), the caller just
    falls through to that normal pipeline as if this had never run."""
    result = await probe_playlist(req.url)
    if not result["is_playlist"]:
        return {"is_playlist": False}

    tag = ""
    if req.tag_domain:
        domain = get_domain(req.url)
        if domain:
            tag = f" [{domain}]"

    entries = [
        {"url": e["url"], "title": _apply_title_prefix(clean_filename(e["title"]) + tag)}
        for e in result["entries"]
    ]
    return {
        "is_playlist": True,
        "playlist_title": result["playlist_title"],
        "entries": entries,
    }


@app.post("/api/playlist/queue")
async def api_queue_playlist(req: PlaylistQueueRequest):
    """Queues every entry of a playlist already returned by
    /api/playlist/probe - titles are taken as-is (already cleaned/tagged
    by the probe step), no per-item edit step. Downloads at most
    PLAYLIST_CONCURRENCY at a time, independent of any other playlist
    queued separately."""
    entries = [{"url": e.url, "title": e.title} for e in req.entries]
    # Capture the configured download folder at playlist initiation time.
    # Every item in this batch keeps this destination even if the user changes
    # the app folder setting while the playlist is still downloading.
    return await job_manager.start_playlist_batch(entries, req.res_cap, get_save_dir(), req.number_titles)


@app.post("/api/jobs")
async def api_start_job(req: StartJobRequest):
    filename = clean_filename(req.filename)
    if not filename:
        return JSONResponse(status_code=400, content={"error": "filename required"})
    await job_manager.start_job(req.url, filename, req.res_cap, req.original_pasted_url)
    return {"ok": True, "filename": filename}


@app.post("/api/jobs/cancel")
async def api_cancel_job(req: CancelRequest):
    ok = await job_manager.cancel_job(req.filename)
    return {"ok": ok}


@app.post("/api/jobs/retry")
async def api_retry_job(req: CancelRequest):
    job = await job_manager.retry_job(req.filename)
    if job is None:
        return JSONResponse(status_code=400, content={"error": "That item isn't retryable."})
    return {"ok": True, "job": job}


@app.post("/api/jobs/delete")
async def api_delete_job(req: CancelRequest):
    ok = job_manager.delete_job(req.filename)
    if ok:
        await job_manager.connections.broadcast({"type": "job_deleted", "filename": req.filename})
    return {"ok": ok}


@app.post("/api/jobs/delete-batch")
async def api_delete_jobs_batch(req: BatchFilenamesRequest):
    """Multi-select version of delete: deletes each given filename,
    skipping (not deleting) anything currently DOWNLOADING."""
    result = job_manager.delete_jobs(req.filenames)
    for filename in result["deleted"]:
        await job_manager.connections.broadcast({"type": "job_deleted", "filename": filename})
    return result


@app.post("/api/jobs/open-folder")
async def api_open_folder(req: CancelRequest, request: Request):
    """Opens the OS file browser at the file's location. Gated to
    localhost requests - a window popping open on the server machine is
    useless (and confusing) if you're browsing in from your phone."""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse(
            status_code=400,
            content={"error": "Only works when the browser is on the same machine as the server."},
        )

    media_path = find_media_file(req.filename)
    if not media_path:
        return JSONResponse(status_code=404, content={"error": "File not found."})

    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", media_path], **NO_CONSOLE_KWARGS)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", media_path], **NO_CONSOLE_KWARGS)
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(media_path)], **NO_CONSOLE_KWARGS)
        return {"ok": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/jobs/open-with")
async def api_open_with(req: OpenWithRequest, request: Request):
    """Launches a configured external program against a completed job's
    media file. Gated to localhost - same reasoning as open-folder: a
    program window popping open on the server machine is useless (and
    confusing) if you're browsing in from your phone."""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse(
            status_code=400,
            content={"error": "Only works when the browser is on the same machine as the server."},
        )

    program = get_external_program(req.program_id)
    if not program:
        return JSONResponse(status_code=404, content={"error": "That external program no longer exists."})

    media_path = find_media_file(req.filename)
    if not media_path:
        return JSONResponse(status_code=404, content={"error": "File not found."})

    if not os.path.isfile(program["path"]):
        return JSONResponse(
            status_code=400,
            content={"error": f"'{program['name']}' points at a path that no longer exists: {program['path']}"},
        )

    cmd = build_open_with_command(program["path"], program.get("args", ""), media_path)
    try:
        subprocess.Popen(cmd, **NO_CONSOLE_KWARGS)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    return {"ok": True}


@app.post("/api/jobs/rename")
async def api_rename_job(req: RenameRequest):
    try:
        new_name = job_manager.rename_job(req.filename, req.new_filename)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    snapshot = job_manager.snapshot()
    await job_manager.connections.broadcast({"type": "refresh", "jobs": snapshot})
    return {"ok": True, "new_filename": new_name, "jobs": snapshot}


@app.post("/api/jobs/playback-position")
async def api_set_playback_position(req: PlaybackPositionRequest):
    """Fire-and-forget from the player's periodic/pause/close events -
    no broadcast to other clients, since it's high-frequency and each
    client picks up the latest saved position next time it opens the
    player anyway."""
    ok = job_manager.set_playback_position(req.filename, req.position, req.completed)
    return {"ok": ok}


@app.post("/api/jobs/extract-audio")
async def api_extract_audio(req: CancelRequest):
    try:
        new_job = await job_manager.extract_audio(req.filename)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except RuntimeError as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

    await job_manager.connections.broadcast({"type": "job_added", "job": new_job})
    return {"ok": True, "job": new_job}


@app.get("/api/jobs/sync-audio/settings")
async def api_get_sync_audio_settings():
    return {"clip_duration_s": audio_sync.CLIP_DURATION_S}


class SyncAudioSettingsRequest(BaseModel):
    clip_duration_s: float


@app.post("/api/jobs/sync-audio/settings")
async def api_set_sync_audio_settings(req: SyncAudioSettingsRequest):
    try:
        value = set_sync_clip_duration(req.clip_duration_s)
        audio_sync.CLIP_DURATION_S = value
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    return {"ok": True, "clip_duration_s": audio_sync.CLIP_DURATION_S}


@app.post("/api/jobs/sync-audio/create-clip")
async def api_sync_audio_create_clip(req: SyncClipCreateRequest):
    if _has_reencode_twin(req.filename):
        return JSONResponse(status_code=400, content={
            "error": "This file already has a re-encoded version - remove it before synchronizing audio.",
        })
    try:
        info = await audio_sync.create_clip(job_manager, req.filename, req.start_seconds, req.delay_ms)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except RuntimeError as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    return {"ok": True, "render": info}


@app.post("/api/jobs/sync-audio/apply-clip-delay")
async def api_sync_audio_apply_clip_delay(req: SyncAudioApplyRequest):
    try:
        info = await audio_sync.apply_clip_delay(job_manager, req.filename, req.delay_ms)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except RuntimeError as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    return {"ok": True, "render": info}


@app.post("/api/jobs/sync-audio/redo-clip")
async def api_sync_audio_redo_clip(req: CancelRequest):
    try:
        await audio_sync.redo_clip(job_manager, req.filename)
    except RuntimeError as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    return {"ok": True}


@app.post("/api/jobs/sync-audio/render-full")
async def api_sync_audio_render_full(req: SyncAudioApplyRequest):
    if _has_reencode_twin(req.filename):
        return JSONResponse(status_code=400, content={
            "error": "This file already has a re-encoded version - remove it before synchronizing audio.",
        })
    try:
        info = await audio_sync.render_full(job_manager, req.filename, req.delay_ms)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except RuntimeError as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    return {"ok": True, "render": info}


@app.post("/api/jobs/sync-audio/accept")
async def api_sync_audio_accept(req: SyncAudioConfirmRequest):
    try:
        job = await audio_sync.accept(job_manager, req.filename, req.delay_ms)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except RuntimeError as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    await job_manager.connections.broadcast({"type": "refresh", "jobs": job_manager.snapshot()})
    return {"ok": True, "job": job}


@app.post("/api/jobs/sync-audio/discard-full")
async def api_sync_audio_discard_full(req: CancelRequest):
    try:
        await audio_sync.discard_full(job_manager, req.filename)
    except RuntimeError as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    return {"ok": True}


@app.post("/api/jobs/sync-audio/cancel")
async def api_sync_audio_cancel(req: CancelRequest):
    await audio_sync.cancel(job_manager, req.filename)
    return {"ok": True}


@app.get("/api/jobs/thumbnail")
async def api_job_thumbnail(filename: str):
    path = await get_thumbnail_path(filename)
    if not path:
        return JSONResponse(status_code=404, content={"error": "No thumbnail available."})
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/jobs/stream")
async def api_stream_video(filename: str, request: Request, source: str = "original"):
    """Serves the video with HTTP Range support. This isn't optional
    polish - mobile Safari in particular refuses to play video at all
    unless the server responds correctly to Range requests, and desktop
    browsers need it for seeking.

    Uses async generators (not plain sync generators) deliberately: when
    a connection ends early - e.g. the player is closed mid-stream -
    Starlette can cleanly cancel an async generator via aclose(), which
    runs our try/finally and closes the file immediately. A sync
    generator would run in a background thread pool instead, where
    cancellation on disconnect is much less reliable - the file could
    stay open in that thread well after the request ended, which on
    Windows means the file stays locked (can't be deleted/moved) until
    something else forces it closed.
    """
    sync_path = audio_sync.resolve_stream_path(filename, source)
    if sync_path is not None:
        media_path = sync_path
    elif source == "converted":
        media_path = find_converted_file(filename)
    else:
        media_path = find_media_file(filename)
    if not media_path or not os.path.isfile(media_path):
        return JSONResponse(status_code=404, content={"error": "File not found."})

    file_size = os.path.getsize(media_path)
    media_type = mimetypes.guess_type(media_path)[0] or "video/mp4"
    range_header = request.headers.get("range")
    # Exposed so the frontend can show the literal on-disk filename (with
    # extension) of whatever is actually loaded in the sync UI's video
    # player - the sync workflow's in-progress files (clip/staging) have
    # names that differ from the job's own filename, and both those and
    # confirmed re-encode twins live in Converted/, so prefix that when
    # applicable to disambiguate from an original of the same basename.
    media_filename = os.path.basename(media_path)
    if os.path.normpath(os.path.dirname(media_path)) == os.path.normpath(get_converted_dir()):
        media_filename = f"{CONVERTED_DIR_NAME}/{media_filename}"
    # HTTP header values have to be Latin-1-encodable; a title with an
    # emoji or other non-Latin-1 character in it (playlist titles come
    # through unsanitized aside from stripping Windows-illegal path
    # characters, so this happens routinely) would otherwise crash this
    # response entirely - not something the frontend could work around,
    # since it never gets a chance to see the header at all. Percent-
    # encoding round-trips exactly and is always header-safe; the one
    # consumer (setSyncPlayerSource in app.js) decodes it back.
    media_filename_header = quote(media_filename)

    if range_header:
        try:
            range_spec = range_header.strip().lower().replace("bytes=", "")
            start_str, _, end_str = range_spec.partition("-")
            start = int(start_str) if start_str else 0
            end = int(end_str) if end_str else file_size - 1
            end = min(end, file_size - 1)
        except ValueError:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{file_size}"})

        if start > end or start >= file_size:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{file_size}"})

        chunk_length = end - start + 1

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(chunk_length),
            "X-Media-Filename": media_filename_header,
        }
        return StreamingResponse(
            _iter_file_range(media_path, start, chunk_length),
            status_code=206, media_type=media_type, headers=headers,
        )

    headers = {"Accept-Ranges": "bytes", "Content-Length": str(file_size), "X-Media-Filename": media_filename_header}
    return StreamingResponse(
        _iter_file_range(media_path, 0, file_size),
        status_code=200, media_type=media_type, headers=headers,
    )


async def _iter_file_range(media_path: str, start: int, length: int):
    f = await asyncio.to_thread(open, media_path, "rb")
    try:
        if start:
            await asyncio.to_thread(f.seek, start)
        remaining = length
        while remaining > 0:
            data = await asyncio.to_thread(f.read, min(STREAM_CHUNK_SIZE, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data
    finally:
        await asyncio.to_thread(f.close)


@app.post("/api/history-search")
async def api_history_search(req: HistorySearchRequest):
    return {"url": search_history(req.query)}


@app.post("/api/history-lookup")
async def api_history_lookup(req: HistoryLookupRequest):
    """For external programs: given a download folder and a filename,
    return the exact matching URL from that folder's history log - not
    necessarily the server's currently active save_dir. Unlike
    /api/history-search (fuzzy, current folder only), this is an exact,
    extension-insensitive match against a caller-specified folder. No
    localhost gating - it's a read-only log lookup, same trust level as
    the rest of the ledger API."""
    try:
        url = lookup_history_in_folder(req.folder, req.filename)
    except HistoryLookupError as e:
        return JSONResponse(status_code=404, content={"error": str(e)})
    return {"url": url}


@app.get("/api/history")
async def api_get_history():
    """Full download history for Search History Mode - every entry ever
    logged, newest first, regardless of whether the file is still on
    disk (it may have since been moved, renamed, or deleted)."""
    return {"entries": list(reversed(get_history_entries()))}


@app.post("/api/history/delete")
async def api_delete_history_entry(req: HistoryDeleteRequest):
    """Removes a single record from the history log. This only removes
    the log line - it never touches any file on disk."""
    ok = delete_history_entry(req.timestamp, req.filename, req.url)
    if ok:
        await connections.broadcast({
            "type": "history_entry_deleted",
            "timestamp": req.timestamp, "filename": req.filename, "url": req.url,
        })
    return {"ok": ok}


@app.post("/api/refresh")
async def api_refresh():
    done_jobs = scan_filesystem()
    await job_manager.seed_from_filesystem(done_jobs, replace=True)
    snapshot = job_manager.snapshot()
    await job_manager.connections.broadcast({"type": "refresh", "jobs": snapshot})
    return {"jobs": snapshot}


# ── Encode Manager ────────────────────────────────────────────────
def _resolve_source_path(filename: str) -> str:
    """Resolves an encode job's source filename to an actual path on
    disk via the download ledger. Raises ValueError if it can't be
    found - e.g. the folder changed since the card was last rendered."""
    media_path = find_media_file(filename)
    if not media_path:
        raise ValueError(f"Couldn't find '{filename}' in the current download folder.")
    return media_path


def _is_localhost(request: Request) -> bool:
    client_host = request.client.host if request.client else ""
    return client_host in ("127.0.0.1", "::1", "localhost")


def _open_in_explorer(path: str):
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", path], **NO_CONSOLE_KWARGS)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path], **NO_CONSOLE_KWARGS)
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path)], **NO_CONSOLE_KWARGS)


@app.get("/api/encode/capabilities")
async def api_encode_capabilities():
    return await encode_manager.get_capabilities()


@app.get("/api/encode/jobs")
async def api_get_encode_jobs():
    return encode_manager.snapshot()


@app.post("/api/encode/probe")
async def api_encode_probe(req: EncodeSourceRequest):
    try:
        source_path = _resolve_source_path(req.filename)
        info = await encode_manager.probe_source(source_path)
    except (ValueError, RuntimeError) as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    info["source_path"] = source_path
    return info


@app.post("/api/encode/estimate")
async def api_encode_estimate(req: EncodeEstimateRequest):
    try:
        source_path = _resolve_source_path(req.filename)
        source_info = await encode_manager.probe_source(source_path)
    except (ValueError, RuntimeError) as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    options = req.options.dict()
    if options.get("mode") == "size":
        estimated_bytes = int(float(options.get("target_size_mb") or 0) * 1024 * 1024)
    else:
        estimated_bytes = encode_manager.estimate(source_info=source_info, options=options)
    return {
        "estimated_bytes": estimated_bytes,
        "estimated_size_label": format_file_size(estimated_bytes) if estimated_bytes else "",
        "source_info": source_info,
    }


@app.post("/api/encode/jobs")
async def api_enqueue_encode_job(req: EnqueueEncodeRequest):
    if job_manager.jobs.get(req.filename, {}).get("synchronized"):
        return JSONResponse(status_code=400, content={
            "error": "This file already has a synchronized-audio version - remove it before re-encoding.",
        })
    try:
        source_path = _resolve_source_path(req.filename)
        job = await encode_manager.enqueue(source_path, req.options.dict())
    except (ValueError, RuntimeError) as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    return {"ok": True, "job": job}


@app.post("/api/encode/jobs/cancel")
async def api_cancel_encode_job(req: EncodeJobIdRequest):
    ok = encode_manager.cancel_job(req.job_id)
    return {"ok": ok}


@app.post("/api/encode/jobs/retry")
async def api_retry_encode_job(req: EncodeJobIdRequest):
    try:
        job = await encode_manager.retry_job(req.job_id)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    return {"ok": True, "job": job}


@app.post("/api/encode/jobs/delete")
async def api_delete_encode_job(req: EncodeDeleteRequest):
    try:
        ok = encode_manager.delete_job(req.job_id, delete_output=req.delete_output)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    if ok:
        await connections.broadcast({"type": "encode_job_deleted", "job_id": req.job_id})
    return {"ok": ok}


@app.post("/api/encode/jobs/move-up")
async def api_move_up_encode_job(req: EncodeJobIdRequest):
    ok = encode_manager.move_up(req.job_id)
    return {"ok": ok}


@app.post("/api/encode/jobs/open-folder")
async def api_open_encode_job_folder(req: EncodeJobIdRequest, request: Request):
    """Opens the OS file browser at a finished encode's output file.
    Localhost-gated, same reasoning as the download ledger's
    open-folder endpoint."""
    if not _is_localhost(request):
        return JSONResponse(
            status_code=400,
            content={"error": "Only works when the browser is on the same machine as the server."},
        )
    job = encode_manager.jobs.get(req.job_id)
    if not job or not os.path.isfile(job.get("output_path", "")):
        return JSONResponse(status_code=404, content={"error": "Output file not found."})
    try:
        _open_in_explorer(job["output_path"])
        return {"ok": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/encode/open-converted-folder")
async def api_open_converted_folder(request: Request):
    """Localhost-gated, same reasoning as the other open-folder
    endpoints."""
    if not _is_localhost(request):
        return JSONResponse(
            status_code=400,
            content={"error": "Only works when the browser is on the same machine as the server."},
        )
    converted_dir = get_converted_dir()
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", converted_dir], **NO_CONSOLE_KWARGS)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", converted_dir], **NO_CONSOLE_KWARGS)
        else:
            subprocess.Popen(["xdg-open", converted_dir], **NO_CONSOLE_KWARGS)
        return {"ok": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ── WebSocket ────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await job_manager.connections.connect(ws)
    try:
        while True:
            # Frontend doesn't need to send anything; just keep the
            # connection open and detect disconnects.
            await ws.receive_text()
    except WebSocketDisconnect:
        job_manager.connections.disconnect(ws)
    except Exception:
        job_manager.connections.disconnect(ws)


# ── Static frontend ──────────────────────────────────────────────
@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.post("/api/app/restart")
async def api_restart_app():
    """Relaunches the app (works whether it was started via
    `python backend/main.py` directly or via tray_launcher.py, since
    sys.argv[0] reflects whichever script was actually invoked) and
    exits this process.

    Two-stage relaunch to avoid a port-binding race: a tiny detached
    helper process waits briefly, THEN starts the real app - by which
    point this process has already exited and released the port. If we
    spawned the real app immediately instead, it could try to bind
    before this process's socket is actually closed and fail to start.
    """
    asyncio.create_task(_do_restart())
    return {"ok": True}


async def _do_restart():
    await asyncio.sleep(0.3)  # let the HTTP response above actually reach the client first

    if FROZEN:
        # The exe is fully self-contained - no separate script to pass it.
        relaunch_cmd = [sys.executable]
        relaunch_cwd = os.path.dirname(sys.executable)
    else:
        launch_script = os.path.abspath(sys.argv[0])
        relaunch_cmd = [sys.executable, launch_script]
        relaunch_cwd = os.path.dirname(launch_script)

    # Rather than a separate helper process sleeping before relaunching
    # (which needs a real Python interpreter to run a snippet - not
    # available for a frozen exe, since sys.executable there IS the app,
    # not python.exe), tell the relaunched process itself to pause
    # briefly before binding. Works identically frozen or not.
    env = os.environ.copy()
    env["STASH_DLP_STARTUP_DELAY"] = "1.5"

    # Strip PyInstaller's bootloader-internal vars (e.g. _MEIPASS2) if
    # present. Copying them into a relaunched frozen exe tells it to
    # reuse the PARENT's temp extraction folder instead of doing its own
    # - and since the parent exits almost immediately after spawning the
    # child, that folder can be mid-cleanup, causing binary extension
    # modules (pydantic_core, etc.) to go missing in the new process.
    for _key in [k for k in env if k.startswith("_MEI")]:
        del env[_key]

    try:
        subprocess.Popen(
            relaunch_cmd,
            cwd=relaunch_cwd,
            env=env,
            close_fds=True,
            **NO_CONSOLE_KWARGS,
        )
    except Exception:
        pass  # if this fails, at least don't hang - the process still exits below

    os._exit(0)  # not graceful, but simplest reliable exit; WS clients auto-reconnect


if __name__ == "__main__":
    import time
    import uvicorn

    _startup_delay = float(os.environ.get("STASH_DLP_STARTUP_DELAY", "0") or 0)
    if _startup_delay > 0:
        time.sleep(_startup_delay)

    uvicorn.run(app, host=HOST, port=PORT, reload=False)
