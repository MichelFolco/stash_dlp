"""Stash integration for Stash DLP.

This module contains all functionality specific to importing a media file
from a Stash scene and replacing the original Stash source after processing.
No general Stash metadata is imported, except the scene's tags: every tag on
the scene is fetched and stashed on the job at import time (job["stash_tags"]),
so the Replace Stash Source modal can later offer any of them for deletion
from the scene once the source file is replaced. Separately, when an import
originates from a Check Tag search, that one tag's id/name are also stashed
under stash_tag_id/stash_tag_name purely so the UI can show a pill for it and
lock renaming - unrelated to the full tag list above.
"""

import asyncio
import json
import os
import re
import shutil
import urllib.error
import urllib.request
from typing import Optional
from urllib.parse import unquote

STASH_HOST = os.environ.get("STASH_HOST", "http://localhost:9999").rstrip("/")
from audio_sync import FILE_OP_RETRIES, FILE_OP_RETRY_DELAY, _is_lock_error, _remove_with_retry
from ffmpeg_encode import probe_basic_info
from job_manager import NeedsDecisionError
from settings import get_save_dir
from storage import save_queue_to_disk
from thumbnails import thumbnail_path_for
from ytdlp_utils import clean_filename, find_converted_file, find_media_file, format_file_size, is_audio_file


def _safe_getsize(path: str) -> int:
    """Best-effort file size for display in the reencode/sync decision
    prompt - a momentary Windows sharing violation on a file that was
    just written (a fresh sync twin right after Confirm Sync, or a
    fresh Encode Manager output) shouldn't block showing the prompt."""
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


async def _copy_or_move_with_retry(op, src: str, dst: str) -> None:
    """Runs shutil.copy2/shutil.move with the same Windows-sharing-
    violation retry tolerance audio_sync.py's own file ops use - a file
    that was just written (e.g. a sync twin moments after Confirm Sync)
    can still be transiently locked by antivirus/indexing right after
    the write completes, and a single-shot attempt was failing the
    whole Replace Stash Source operation on exactly that timing."""
    last_err = None
    for _ in range(FILE_OP_RETRIES):
        try:
            op(src, dst)
            return
        except OSError as e:
            if not _is_lock_error(e):
                raise
            last_err = e
            await asyncio.sleep(FILE_OP_RETRY_DELAY)
    raise OSError(
        f"'{os.path.basename(src)}' is still in use (likely still open in the preview "
        f"player) - close the preview and try again. Details: {last_err}"
    )


def _scene_id(scene_input: str) -> str:
    raw = (scene_input or "").strip()
    if not raw:
        raise ValueError("Please enter a Stash scene URL or scene ID.")
    match = re.search(r"/scenes/(\d+)", raw)
    scene_id = match.group(1) if match else (raw if raw.isdigit() else None)
    if not scene_id:
        raise ValueError("Could not find a numeric Stash scene ID in that URL.")
    return scene_id


async def _fetch_scene_details(scene_id: str) -> dict:
    """Fetches both the scene's file path and its full tag list in one
    round trip. The tag list is stored verbatim on the job at import time
    (see import_stash_scene) so the Replace Stash Source modal can later
    offer every one of them for deletion, not just a tag used to find the
    scene via Check Tag."""
    query = json.dumps({
        "query": f'{{ findScene(id: "{scene_id}") {{ files {{ path }} tags {{ id name }} }} }}'
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{STASH_HOST}/graphql",
        data=query,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", str(e))
        raise RuntimeError(f"Could not reach Stash at {STASH_HOST}: {reason}")
    except Exception as e:
        raise RuntimeError(f"Could not query Stash: {e}")

    scene = data.get("data", {}).get("findScene") or {}
    files = scene.get("files") or []
    if not files:
        raise ValueError(f"Scene {scene_id} was not found or has no files.")
    source_path = files[0].get("path", "")
    if not source_path:
        raise ValueError("Stash returned an empty file path.")
    tags = [
        {"id": t.get("id"), "name": t.get("name", "")}
        for t in (scene.get("tags") or [])
        if t.get("id")
    ]
    return {
        "path": os.path.abspath(os.path.normpath(unquote(source_path))),
        "tags": tags,
    }


async def _graphql_query(query: str, variables: Optional[dict] = None) -> dict:
    """Run a GraphQL query/mutation against Stash using variables (never raw
    string interpolation of user input, unlike the scene-id lookup above)."""
    body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    request = urllib.request.Request(
        f"{STASH_HOST}/graphql",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", str(e))
        raise RuntimeError(f"Could not reach Stash at {STASH_HOST}: {reason}")
    except Exception as e:
        raise RuntimeError(f"Could not query Stash: {e}")

    if payload.get("errors"):
        message = "; ".join(err.get("message", "Unknown error") for err in payload["errors"])
        raise RuntimeError(f"Stash returned an error: {message}")
    return payload.get("data") or {}


async def is_stash_running() -> bool:
    """Lightweight liveness check for the Stash server: a short-timeout
    GraphQL ping, used to decide whether to surface the Stash button in
    the UI at all. Any failure (unreachable host, connection refused,
    timeout, non-GraphQL response) is treated as "not running" - this
    is a presence check, not a diagnostic, so errors are swallowed
    rather than raised.
    """
    query = json.dumps({"query": "{ systemStatus { status } }"}).encode("utf-8")
    request = urllib.request.Request(
        f"{STASH_HOST}/graphql",
        data=query,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        def _do_request():
            with urllib.request.urlopen(request, timeout=2) as response:
                return json.loads(response.read().decode("utf-8"))
        payload = await asyncio.to_thread(_do_request)
    except Exception:
        return False
    return not payload.get("errors")


async def find_scenes_by_tag(tag_name: str) -> dict:
    """Look up a Stash tag by name and list every scene tagged with it.

    Only the fields needed to identify and open each scene are requested
    (id, title, date, file path) -- no other scene metadata is imported.
    """
    query_text = (tag_name or "").strip()
    if not query_text:
        raise ValueError("Please enter a Stash tag name.")

    tags_query = """
        query FindTag($q: String!) {
          findTags(filter: { q: $q, per_page: 20 }) {
            count
            tags { id name }
          }
        }
    """
    tags_data = await _graphql_query(tags_query, {"q": query_text})
    tags = (tags_data.get("findTags") or {}).get("tags") or []
    match = next((t for t in tags if (t.get("name") or "").lower() == query_text.lower()), None)
    if not match:
        if tags:
            suggestions = ", ".join(t.get("name", "") for t in tags[:8])
            raise ValueError(f'No exact tag named "{query_text}". Did you mean: {suggestions}?')
        raise ValueError(f'No Stash tag named "{query_text}" was found.')

    tag_id = match["id"]
    tag_name_resolved = match["name"]

    scenes_query = """
        query FindScenesByTag($tagId: ID!) {
          findScenes(
            scene_filter: { tags: { value: [$tagId], modifier: INCLUDES } }
            filter: { per_page: -1, sort: "path", direction: ASC }
          ) {
            count
            scenes {
              id
              title
              date
              files { path }
            }
          }
        }
    """
    scenes_data = await _graphql_query(scenes_query, {"tagId": tag_id})
    found = scenes_data.get("findScenes") or {}
    scenes_raw = found.get("scenes") or []

    scenes = []
    for scene in scenes_raw:
        files = scene.get("files") or []
        raw_path = files[0].get("path", "") if files else ""
        path = os.path.normpath(unquote(raw_path)) if raw_path else ""
        filename = os.path.basename(path) if path else ""
        title = scene.get("title") or filename or f"Scene {scene.get('id')}"
        scenes.append({
            "id": scene.get("id"),
            "title": title,
            "date": scene.get("date") or "",
            "path": path,
            "url": f"{STASH_HOST}/scenes/{scene.get('id')}",
        })

    return {
        "tag_id": tag_id,
        "tag_name": tag_name_resolved,
        "count": found.get("count", len(scenes)),
        "scenes": scenes,
    }


async def import_stash_scene(
    manager, scene_input: str, tag_id: Optional[str] = None, tag_name: Optional[str] = None
) -> dict:
    """Import the first media file belonging to a Stash scene.

    Only ``files.path`` is requested from GraphQL. No Stash tags or other
    scene metadata are imported, except that when this import came from a
    Check Tag result (``tag_id``/``tag_name`` provided by the caller), that
    single tag's id/name are recorded on the job so the UI can show a pill
    for it and, later, offer to remove just that tag from the scene when
    the source is replaced.
    """
    scene_id = _scene_id(scene_input)
    details = await _fetch_scene_details(scene_id)
    source_path = details["path"]
    stash_tags = details["tags"]

    if not os.path.isfile(source_path):
        raise ValueError(f"Stash file does not exist on the server: {source_path}")

    original_filename = os.path.basename(source_path)
    stem = clean_filename(os.path.splitext(original_filename)[0])
    if not stem:
        raise ValueError("The Stash file has no usable filename after cleaning.")
    if stem in manager.jobs:
        raise ValueError(f"'{stem}' is already in the ledger.")

    save_dir = get_save_dir()
    os.makedirs(save_dir, exist_ok=True)
    extension = os.path.splitext(original_filename)[1]
    dest_path = os.path.join(save_dir, stem + extension)
    if os.path.exists(dest_path):
        raise ValueError(f"A file named '{os.path.basename(dest_path)}' already exists in the download folder.")

    try:
        await asyncio.to_thread(shutil.copy2, source_path, dest_path)
    except OSError as e:
        raise ValueError(f"Could not copy the Stash file: {e}")

    try:
        probed = await probe_basic_info(dest_path)
    except Exception:
        probed = {"width": 0, "height": 0, "duration": 0.0, "video_codec": "", "audio_codec": ""}

    size = os.path.getsize(dest_path)
    is_audio = is_audio_file(dest_path)
    ext = extension.lstrip(".").upper()
    stash_url = f"{STASH_HOST}/scenes/{scene_id}"

    job = {
        "filename": stem,
        "url": stash_url,
        "res_cap": "Imported",
        "status": "DONE",
        "file_size": format_file_size(size),
        "is_audio": is_audio,
        "playback_position": 0,
        "width": probed.get("width", 0),
        "height": probed.get("height", 0),
        "duration": probed.get("duration", 0),
        "ext": ext,
        "video_codec": probed.get("video_codec", ""),
        "audio_codec": probed.get("audio_codec", ""),
        "pct": 100, "total": "", "speed": "", "eta": "",
        "source_type": "stash",
        "source_path": source_path,
        "stash_scene_id": scene_id,
        "stash_scene_url": stash_url,
        "stash_tag_id": tag_id or None,
        "stash_tag_name": tag_name or None,
        "stash_tags": stash_tags,
    }
    manager.jobs[stem] = job
    manager.saved_queue[stem] = {
        key: job[key]
        for key in (
            "url", "res_cap", "status", "file_size", "is_audio", "width", "height",
            "duration", "ext", "video_codec", "audio_codec", "source_type",
            "source_path", "stash_scene_id", "stash_scene_url",
            "stash_tag_id", "stash_tag_name", "stash_tags",
        )
    }
    save_queue_to_disk(manager.saved_queue)
    await manager.connections.broadcast({"type": "job_added", "job": job})
    return job


async def remove_tags_from_scene(scene_id: str, tag_ids: list) -> None:
    """Removes any number of tags from a scene's tag list in Stash, in a
    single fetch-then-write round trip.

    Stash's ``sceneUpdate`` mutation replaces the scene's whole tag list
    rather than patching individual entries, so the current list is
    fetched first and every id in ``tag_ids`` filtered out before writing
    the remainder back.
    """
    if not tag_ids:
        return
    to_remove = set(tag_ids)

    query = """
        query SceneTags($id: ID!) {
          findScene(id: $id) { tags { id } }
        }
    """
    data = await _graphql_query(query, {"id": scene_id})
    scene = data.get("findScene") or {}
    current_tag_ids = [t["id"] for t in (scene.get("tags") or [])]
    remaining = [t for t in current_tag_ids if t not in to_remove]
    if len(remaining) == len(current_tag_ids):
        return  # none of the requested tags are on the scene, nothing to do

    mutation = """
        mutation RemoveSceneTags($id: ID!, $tagIds: [ID!]) {
          sceneUpdate(input: { id: $id, tag_ids: $tagIds }) { id }
        }
    """
    await _graphql_query(mutation, {"id": scene_id, "tagIds": remaining})


async def replace_source(
    manager, filename: str, variant: Optional[str] = None, delete_tag_ids: Optional[list] = None
) -> dict:
    """Replace an imported Stash source with the selected processed version.

    ``delete_tag_ids`` may name any subset of the scene's tags (as stored
    on the job at import time in job["stash_tags"]) - not just the single
    tag that originally matched a Check Tag search.

    Returns a status dict describing which of the requested tags were
    actually deleted, so the caller can report a partial-success message:
    the file replacement below either fully succeeds or raises, but tag
    deletion is treated as best-effort since the file swap has already
    happened by the time it's attempted.
    """
    job = manager.jobs.get(filename)
    if not job or job.get("status") != "DONE":
        raise ValueError(f"'{filename}' isn't a completed item.")

    source_path = job.get("source_path")
    if job.get("source_type") != "stash" or not source_path:
        raise ValueError("This item was not imported from Stash.")
    if not os.path.isfile(source_path):
        raise ValueError(f"The original source file no longer exists: {source_path}")

    media_path = find_media_file(filename)
    if not media_path:
        raise ValueError(f"Couldn't find the working file for '{filename}'.")
    converted_path = find_converted_file(filename)

    if converted_path and variant is None:
        original_info = await probe_basic_info(media_path)
        reencoded_info = await probe_basic_info(converted_path)
        raise NeedsDecisionError({
            "filename": filename,
            "kind": "synchronized" if job.get("synchronized") else "reencoded",
            "original": {
                "size_bytes": _safe_getsize(media_path),
                "size_label": format_file_size(_safe_getsize(media_path)),
                "width": original_info["width"], "height": original_info["height"],
            },
            "reencoded": {
                "size_bytes": _safe_getsize(converted_path),
                "size_label": format_file_size(_safe_getsize(converted_path)),
                "width": reencoded_info["width"], "height": reencoded_info["height"],
            },
        })

    chosen = converted_path if converted_path and variant == "reencoded" else media_path
    src_ext = os.path.splitext(chosen)[1]
    source_ext = os.path.splitext(source_path)[1]

    try:
        if src_ext.lower() == source_ext.lower():
            await _copy_or_move_with_retry(shutil.copy2, chosen, source_path)
        else:
            new_source = os.path.splitext(source_path)[0] + src_ext
            if os.path.exists(new_source) and os.path.abspath(new_source) != os.path.abspath(source_path):
                raise ValueError(f"A source file already exists at '{new_source}'.")
            await _copy_or_move_with_retry(shutil.move, chosen, new_source)
            if os.path.exists(source_path):
                await _remove_with_retry(source_path)
            source_path = new_source
    except OSError as e:
        raise ValueError(f"Could not replace the Stash source: {e}")

    for path in (media_path, converted_path):
        if path and os.path.exists(path) and os.path.abspath(path) != os.path.abspath(source_path):
            await _remove_with_retry(path)

    thumb_path = thumbnail_path_for(filename)
    if os.path.exists(thumb_path):
        try:
            os.remove(thumb_path)
        except OSError:
            pass

    scene_id = job.get("stash_scene_id")
    stash_tags = job.get("stash_tags") or []

    manager.jobs.pop(filename, None)
    manager.saved_queue.pop(filename, None)
    save_queue_to_disk(manager.saved_queue)

    result = {"deleted_tag_names": [], "tag_delete_error": None}
    delete_tag_ids = [t for t in (delete_tag_ids or []) if t]
    if delete_tag_ids and scene_id:
        try:
            await remove_tags_from_scene(scene_id, delete_tag_ids)
            id_to_name = {t["id"]: t["name"] for t in stash_tags}
            result["deleted_tag_names"] = [id_to_name.get(t, t) for t in delete_tag_ids]
        except Exception as e:
            result["tag_delete_error"] = str(e)
    return result
