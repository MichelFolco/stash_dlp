import asyncio
import mimetypes
import os
import subprocess
import sys

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import PROJECT_ROOT, HOST, PORT
from diskspace import get_free_space_label
from filesystem_scan import scan_filesystem
from folder_dialog import ask_directory
from job_manager import JobManager
from m3u8_finder import find_m3u8, M3u8NotFound
from procflags import NO_CONSOLE_KWARGS
from settings import (
    get_save_dir, set_save_dir, get_recent_dirs, remove_recent_dir,
    get_target_dir, set_target_dir, get_recent_target_dirs, remove_recent_target_dir,
)
from storage import search_history
from thumbnails import get_thumbnail_path
from ytdlp_utils import clean_filename, fetch_title, get_domain, check_and_update_ytdlp, find_media_file

STREAM_CHUNK_SIZE = 1024 * 1024  # 1 MB

app = FastAPI(title="Stash DLP Web")

STATIC_DIR = os.path.join(str(PROJECT_ROOT), "static")

job_manager = JobManager()

# yt-dlp version state, populated on startup (mirrors ytdlp_version /
# ytdlp_just_updated on the desktop app's main window)
version_state = {"version": "", "just_updated": False}


@app.on_event("startup")
async def on_startup():
    version, just_updated = await check_and_update_ytdlp()
    version_state["version"] = version
    version_state["just_updated"] = just_updated

    done_jobs = scan_filesystem()
    job_manager.seed_from_filesystem(done_jobs)


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


class CancelRequest(BaseModel):
    filename: str


class SaveDirRequest(BaseModel):
    save_dir: str


class TargetDirRequest(BaseModel):
    target_dir: str


class RecentDirRemoveRequest(BaseModel):
    path: str


class RenameRequest(BaseModel):
    filename: str
    new_filename: str


class PlaybackPositionRequest(BaseModel):
    filename: str
    position: float


# ── REST endpoints ──────────────────────────────────────────────
@app.get("/api/version")
async def get_version():
    return version_state


@app.get("/api/settings")
async def api_get_settings():
    save_dir = get_save_dir()
    return {
        "save_dir": save_dir,
        "recent_dirs": get_recent_dirs(),
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
    job_manager.seed_from_filesystem(done_jobs, replace=True)
    snapshot = job_manager.snapshot()
    await job_manager.connections.broadcast({"type": "refresh", "jobs": snapshot})
    return {
        "save_dir": new_path,
        "recent_dirs": get_recent_dirs(),
        "free_space": get_free_space_label(new_path),
        "jobs": snapshot,
    }


@app.post("/api/settings/recent/remove")
async def api_remove_recent_dir(req: RecentDirRemoveRequest):
    return {"recent_dirs": remove_recent_dir(req.path)}


@app.post("/api/target-settings/recent/remove")
async def api_remove_recent_target_dir(req: RecentDirRemoveRequest):
    return {"recent_dirs": remove_recent_target_dir(req.path)}


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


@app.get("/api/target-settings")
async def api_get_target_settings():
    target_dir = get_target_dir()
    return {
        "target_dir": target_dir,
        "recent_dirs": get_recent_target_dirs(),
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
        "recent_dirs": get_recent_target_dirs(),
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


@app.post("/api/jobs/move-to-target")
async def api_move_to_target(req: CancelRequest):
    try:
        job_manager.move_to_target(req.filename)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    await job_manager.connections.broadcast({"type": "job_deleted", "filename": req.filename})
    return {"ok": True}


@app.post("/api/jobs/move-all-to-target")
async def api_move_all_to_target():
    try:
        result = job_manager.move_all_to_target()
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    snapshot = job_manager.snapshot()
    await job_manager.connections.broadcast({"type": "refresh", "jobs": snapshot})
    return {"moved": result["moved"], "failed": result["failed"], "jobs": snapshot}


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
    return {"title": clean_filename(title) + tag, "raw_title": title}


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
        "suggested_title": clean_filename(page_title) + tag,
    }


@app.post("/api/jobs")
async def api_start_job(req: StartJobRequest):
    filename = clean_filename(req.filename)
    if not filename:
        return JSONResponse(status_code=400, content={"error": "filename required"})
    await job_manager.start_job(req.url, filename, req.res_cap, req.original_pasted_url)
    return {"ok": True, "filename": filename}


@app.post("/api/jobs/cancel")
async def api_cancel_job(req: CancelRequest):
    ok = job_manager.cancel_job(req.filename)
    return {"ok": ok}


@app.post("/api/jobs/delete")
async def api_delete_job(req: CancelRequest):
    ok = job_manager.delete_job(req.filename)
    if ok:
        await job_manager.connections.broadcast({"type": "job_deleted", "filename": req.filename})
    return {"ok": ok}


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
    ok = job_manager.set_playback_position(req.filename, req.position)
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


@app.get("/api/jobs/thumbnail")
async def api_job_thumbnail(filename: str):
    path = await get_thumbnail_path(filename)
    if not path:
        return JSONResponse(status_code=404, content={"error": "No thumbnail available."})
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/jobs/stream")
async def api_stream_video(filename: str, request: Request):
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
    media_path = find_media_file(filename)
    if not media_path or not os.path.isfile(media_path):
        return JSONResponse(status_code=404, content={"error": "File not found."})

    file_size = os.path.getsize(media_path)
    media_type = mimetypes.guess_type(media_path)[0] or "video/mp4"
    range_header = request.headers.get("range")

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
        }
        return StreamingResponse(
            _iter_file_range(media_path, start, chunk_length),
            status_code=206, media_type=media_type, headers=headers,
        )

    headers = {"Accept-Ranges": "bytes", "Content-Length": str(file_size)}
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


@app.post("/api/refresh")
async def api_refresh():
    done_jobs = scan_filesystem()
    job_manager.seed_from_filesystem(done_jobs)
    snapshot = job_manager.snapshot()
    await job_manager.connections.broadcast({"type": "refresh", "jobs": snapshot})
    return {"jobs": snapshot}


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

    launch_script = os.path.abspath(sys.argv[0])
    launch_cwd = os.path.dirname(launch_script)
    helper_code = (
        "import subprocess, sys, time; "
        "time.sleep(1.5); "
        f"subprocess.Popen([{sys.executable!r}, {launch_script!r}], cwd={launch_cwd!r})"
    )
    try:
        subprocess.Popen(
            [sys.executable, "-c", helper_code],
            cwd=launch_cwd,
            close_fds=True,
            **NO_CONSOLE_KWARGS,
        )
    except Exception:
        pass  # if this fails, at least don't hang - the process still exits below

    os._exit(0)  # not graceful, but simplest reliable exit; WS clients auto-reconnect


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=HOST, port=PORT, reload=False)
