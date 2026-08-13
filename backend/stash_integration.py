"""Stash integration for Stash DLP.

This module contains all functionality specific to importing a media file
from a Stash scene and replacing the original Stash source after processing.
It intentionally does not request, store, or otherwise handle Stash tags.
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
from ffmpeg_encode import probe_basic_info
from job_manager import NeedsDecisionError
from settings import get_save_dir
from storage import save_queue_to_disk
from thumbnails import thumbnail_path_for
from ytdlp_utils import clean_filename, find_converted_file, find_media_file, format_file_size, is_audio_file


def _scene_id(scene_input: str) -> str:
    raw = (scene_input or "").strip()
    if not raw:
        raise ValueError("Please enter a Stash scene URL or scene ID.")
    match = re.search(r"/scenes/(\d+)", raw)
    scene_id = match.group(1) if match else (raw if raw.isdigit() else None)
    if not scene_id:
        raise ValueError("Could not find a numeric Stash scene ID in that URL.")
    return scene_id


async def _fetch_scene_path(scene_id: str) -> str:
    query = json.dumps({
        "query": f'{{ findScene(id: "{scene_id}") {{ files {{ path }} }} }}'
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
    return os.path.abspath(os.path.normpath(unquote(source_path)))


async def import_stash_scene(manager, scene_input: str) -> dict:
    """Import the first media file belonging to a Stash scene.

    Only ``files.path`` is requested from GraphQL. No Stash tags or other
    scene metadata are imported.
    """
    scene_id = _scene_id(scene_input)
    source_path = await _fetch_scene_path(scene_id)

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
    }
    manager.jobs[stem] = job
    manager.saved_queue[stem] = {
        key: job[key]
        for key in (
            "url", "res_cap", "status", "file_size", "is_audio", "width", "height",
            "duration", "ext", "video_codec", "audio_codec", "source_type",
            "source_path", "stash_scene_id", "stash_scene_url",
        )
    }
    save_queue_to_disk(manager.saved_queue)
    await manager.connections.broadcast({"type": "job_added", "job": job})
    return job


async def replace_source(manager, filename: str, variant: Optional[str] = None) -> None:
    """Replace an imported Stash source with the selected processed version."""
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
                "size_bytes": os.path.getsize(media_path),
                "size_label": format_file_size(os.path.getsize(media_path)),
                "width": original_info["width"], "height": original_info["height"],
            },
            "reencoded": {
                "size_bytes": os.path.getsize(converted_path),
                "size_label": format_file_size(os.path.getsize(converted_path)),
                "width": reencoded_info["width"], "height": reencoded_info["height"],
            },
        })

    chosen = converted_path if converted_path and variant == "reencoded" else media_path
    src_ext = os.path.splitext(chosen)[1]
    source_ext = os.path.splitext(source_path)[1]

    try:
        if src_ext.lower() == source_ext.lower():
            shutil.copy2(chosen, source_path)
        else:
            new_source = os.path.splitext(source_path)[0] + src_ext
            if os.path.exists(new_source) and os.path.abspath(new_source) != os.path.abspath(source_path):
                raise ValueError(f"A source file already exists at '{new_source}'.")
            shutil.move(chosen, new_source)
            if os.path.exists(source_path):
                os.remove(source_path)
            source_path = new_source
    except OSError as e:
        raise ValueError(f"Could not replace the Stash source: {e}")

    for path in (media_path, converted_path):
        if path and os.path.exists(path) and os.path.abspath(path) != os.path.abspath(source_path):
            try:
                os.remove(path)
            except OSError:
                pass

    thumb_path = thumbnail_path_for(filename)
    if os.path.exists(thumb_path):
        try:
            os.remove(thumb_path)
        except OSError:
            pass

    manager.jobs.pop(filename, None)
    manager.saved_queue.pop(filename, None)
    save_queue_to_disk(manager.saved_queue)
