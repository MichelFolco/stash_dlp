## v1.10 - Auto-refresh performance: one Converted/ listing per tick, diffed ledger render
- Backend: `job_manager.seed_from_filesystem()` (run on every `/api/refresh`, including the 5s auto-refresh poll) now lists Converted/ once per call via the new `list_converted_stems()`/`has_converted_twin()` in `ytdlp_utils.py`, instead of calling `find_converted_file()` - and paying its own `os.listdir()` - once per completed job. Turns an O(N jobs) directory-listing cost into O(1) per refresh. `find_converted_file()` itself is unchanged and still used where an actual path is needed (move/replace/stream).
- Frontend: `renderLedger()` no longer tears down and rebuilds every job card on every call. Each job's card is now reused as-is when nothing about it has changed since the last render (tracked via a new `jobCardSignature()` covering every field a card's content or behavior depends on), and only rebuilt when something actually did. Previously every render - including the 5s auto-refresh poll landing on an unchanged queue - reset every thumbnail `<img src>` and flashed the whole ledger for no reason; selection state was already handled outside renderLedger() via `setCardSelectedVisual()`, so it's untouched by this either way.

## v1.09 - Play Twin generalized to has_twin, auto-refresh every 5s
- "Play Converted" (right-click menu) is now "Play Twin" and is gated on `job.has_twin` instead of `isReencoded()`/`isSynchronized()` - so it's offered for a twin from any origin (re-encode, audio sync, or one dropped into Converted/ by hand), not just the two tracked-in-session cases. Same playback behavior as before (streams from Converted/ via `source=converted`).
- Move to Target and Replace Stash Source were already twin-agnostic on the backend (both call `find_converted_file()` directly), so no changes were needed there: non-Stash items can still be moved (prompting Original/Twin whenever a twin exists), and Stash items still only offer Replace Stash Source (same Original/Twin prompt).
- The ledger now auto-refreshes every 5 seconds (same `/api/refresh` the Refresh button triggers, with a re-entrancy guard so a slow response can't stack overlapping calls), so has_twin/HAS TWIN and the rest of the queue stay current without needing a manual click - in-progress downloads are untouched by this, same as a manual Refresh.
- Job card tooltip's "Re-encoded copy available in Converted/" line now reads "Twin copy available in Converted/" and is driven by `has_twin` for the same reason.

## v1.08 - "Has Twin" pill from a direct Converted/ filesystem check
- Added `job["has_twin"]`, set in `job_manager.seed_from_filesystem()` via a direct `find_converted_file()` check against Converted/ - runs on every Refresh press (and at startup), independent of the RE-ENCODED pill's in-memory Encode Manager history or the persisted SYNCHRONIZED flag.
- New "HAS TWIN" pill on the job card, shown only when a twin is found this way but neither RE-ENCODED nor SYNCHRONIZED is already showing - a fallback so a twin left in Converted/ from before a server restart (or dropped in by hand) still surfaces instead of going unnoticed.

## v1.07 - Full tag import, multi-tag cleanup on replace, and Play Converted
- Importing a scene from Stash now saves its complete tag list (`job["stash_tags"]`), not just the single tag from a Check Tag search - persisted through folder refresh/restart via queue.json.
- "Replace Stash Source" now lists every one of those tags as its own checkbox instead of one hardcoded checkbox for the Check Tag tag, so any combination can be removed from the scene in the same fetch-then-`sceneUpdate` GraphQL round trip (now batched into a single request for however many tags are picked).
- Stash-imported items can no longer be moved to the target folder (icon button, right-click menu, and the "Move to Target" bulk action all now exclude them) - moving one out of the download folder orphaned it from Replace Stash Source, since that action needs the working copy to still be there. Use Replace Stash Source instead.
- Added a "Play Converted" item to the file's right-click menu, shown whenever a re-encoded or synchronized twin exists in Converted/ for that item (same detection the RE-ENCODED/SYNCHRONIZED pills already use) - plays the twin directly without needing to go through Replace/Move to pick a variant first.
- Fixed "Replace Stash Source" failing outright for synchronized (and re-encoded) items on a transient Windows sharing violation: the file swap and its size probe now use the same lock-tolerant retry loop `audio_sync.py` already relies on for its own file ops, instead of a single-shot attempt that gave up the moment a file was still momentarily held open (e.g. right after Confirm Sync writes the twin). The endpoint also now returns a real error message instead of a bare 500 on any unexpected failure.

## v1.06 - Stash tag pill, rename lock, recent tags, and tag-cleanup on replace
- Importing a scene from Check Tag results now stamps the item with a pill named after the searched tag (persisted through folder refresh/restart via queue.json).
- Renaming is disabled (both the card's quick-action icon and the right-click Rename item) for any item carrying a Stash tag pill, enforced server-side in `rename_job` as well as in the UI.
- The Check Tag modal now shows the last 5 tags searched as clickable chips; clicking one re-runs the check immediately.
- "Replace Stash Source" is now a proper confirm modal instead of a bare browser confirm(). When the item has a Stash tag pill, it adds a checkbox: `Delete Tag "XXX" from stash scene?` - checking it removes just that tag from the Stash scene (via a fetch-then-`sceneUpdate` GraphQL round trip) after the source file is replaced.

## v1.05 - Sync UI filename prefixed with Converted/ when applicable
- The filename line now shows `Converted/<filename>` whenever the file actually loaded in the player lives in the Converted/ subfolder - which covers the already-synchronized twin, all three in-progress sync render files (clip-src, clip, full-staging), and confirmed re-encode twins. Files from the original download folder still show as a bare filename.

## v1.04 - Sync UI shows the literal filename of whatever is loaded
- The filename line added in v1.03 was showing the job's own filename, not what's actually on disk - during the clip/full-render stages the player is really loaded from a differently-named file (e.g. `<stem>.sync-clip-src.mp4`, `<stem>.sync-clip.mp4`, `<stem>.sync-full-staging.mp4`).
- `/api/jobs/stream` now returns the on-disk basename via an `X-Media-Filename` response header. The frontend reads it with a tiny ranged fetch each time it sets the player source, so the filename line always reflects the literal file (with extension) currently loaded, through every stage.

## v1.03 - Sync UI always shows the loaded filename
- The Synchronize Audio modal's header previously crammed the filename into the title bar, where long names got clipped with an ellipsis - on narrow/mobile views there was often no way to see the full name at all.
- Added a dedicated filename line under the header that wraps instead of truncating, so the full filename stays visible through every stage of the workflow (original, clip, full render).
- Header title is now just "Synchronize Audio"; no backend changes.

## v1.02 - Fixed thumbnail leaking into the download folder
- Root cause: yt-dlp ignores `--paths TYPE:...` whenever the main `-o` outtmpl is an absolute path, and ours always is - so the `--paths thumbnail:...` redirect to the library_data thumbnail cache was silently a no-op, and the thumbnail landed beside the video/audio file in the download folder instead.
- Fixed by giving the `thumbnail` type its own absolute `-o thumbnail:...` outtmpl (pointed at the same path `thumbnails.py` already looks for), which yt-dlp always honors regardless of the main outtmpl. Dropped the now-redundant `--paths thumbnail:...` flag.
- Thumbnails now only ever get written to the central `library_data/.thumbnails/` cache, never the download folder.

## v1.01 - New Encode Job modal reorganized
- Widened the modal to two columns (Quality on the left, Resolution & Correction on the right) so it's much shorter for a given amount of content instead of one long vertical stack.
- Moved the size estimate to a stat strip right under the source info, now showing Original size, Estimated size, and % savings side by side (colored green for a smaller output, red if the encode would end up larger than the source) - visible before touching any settings.
- "Force aspect ratio" presets (16:9 / 4:3 / 21:9 / Custom) are now always visible next to the checkbox instead of hidden until it's checked; picking a preset auto-enables the checkbox.
- Advanced options now expand inline in place with a compact 3-column layout (Audio / Container / Subtitles) plus a combined denoise + oversized-output row, instead of stacking every field vertically.
- No backend changes - the estimate/probe endpoints already returned the source and estimated byte counts needed for the savings %.

## v1.00 - Logo shows app version
- Clicking the logo (top-left, next to the URL field) now flashes the current stash_dlp version in the input placeholder, via a new `/api/app_version` endpoint backed by `APP_VERSION` in `backend/config.py`.
- `APP_VERSION` is the single source of truth for this - bump it in the same edit as any new entry added here (see the comment above its definition in config.py).

## v0.99 - Stash presence indicator
- Added a `/api/stash/status` endpoint that pings Stash's GraphQL API with a short timeout to check whether it's currently running.
- The top row now shows a Stash logo button, only when Stash is detected as running (checked at boot and re-checked every 30s). Clicking it opens the same menu as Ctrl+S (Import from Stash / Check Tag).

## v0.98 - Top row / toolbar polish
- The logo now always sits directly to the left of the URL input, in both desktop and mobile layouts (previously it moved to the button row on mobile).
- Unified the filter toolbar controls (audio filter, hide-completed, sort select, sort direction, select) to the same 32px height with consistent spacing and centered icons.

## v0.97 - Toolbar cleanup
- Removed the "Stash" button from the main control bar; Import from Stash and Check Tag are now reached via Ctrl+S, which opens the same menu the button used to.
- Renamed "Move All to Target" to "Move All".
- Replaced the text "Select" button with a checkbox icon button, moved from the control bar into the filter toolbar.
- On mobile/touch layouts, the URL input is now its own top row, the logo/mode/settings buttons sit on the row below it, and the download/target folder status line is pushed down to the third row.

## v0.96 - Import from tag search results
- Added an "Import" button next to "Open" on each row of the Check Tag results dialog, so a scene can be imported straight from the search results without needing its URL pasted into the separate Import from Stash dialog.

## v0.95 - Stash tag check
- Renamed the "Import from Stash" button to "Stash", now opening a submenu with Import from Stash (unchanged) and a new Check Tag action.
- Check Tag queries the Stash DB for a tag by name and lists every scene that has it (title, path, Open link) in a results dialog.

## v0.94 - Synchronize Audio full-video encoding status
- Changed the Full Video encoding status message to `Synching full video, please wait...`.

## v0.92 - Windows drag cursor

## v0.93
- Fixed Synchronize Audio dialog action buttons remaining disabled after a previous sync job.
- The Create Clip button is now explicitly re-enabled whenever the Synchronize Audio dialog is opened.

- Changed the completed-card drag-and-drop cursor from the grab/hand-style cursor to the standard Windows arrow cursor.

## v0.90 - Synchronize Audio clip duration control
- Added a Synchronize Audio UI control for changing the clip preview duration.
- The value is persisted in app settings and applied to `audio_sync.CLIP_DURATION_S` at runtime.

# Changelog
0.89 Synchronize Audio UI now fills the screen height (video flexes to fill remaining space, no more internal scrolling) and no longer closes on an outside click - only the X, Cancel, Discard/Accept, etc.
0.88 reworked Synchronize Audio into a clip-based flow: Create Clip cuts a fast 10s preview from the playback position instead of re-rendering the whole file on every tweak, Apply Sync/Redo Clip iterate on that clip, Confirm Sync renders the full video into a staging file, and Accept Sync/Discard decide whether it becomes the confirmed twin - previously-confirmed twin is never touched until Accept; all sync file writes/deletes now retry through Windows file-in-use sharing violations instead of failing outright
0.86 added Synchronize Audio: card menu action opens a sync UI (video player, delay input, +/-10/100 dial buttons) to re-render a file with its audio shifted, iterate via Apply, and Confirm to lock in a SYNCHRONIZED twin - mutually exclusive with Re-encode (shared Converted/ twin slot); Transfer Original/Converted prompt now generalized to cover both
0.85 added automatic server restart when project source files change; updated start_tray_lan.bat with hidden file watcher
0.84 added drag-and-drop support for completed download cards to external editing programs
0.83 added a "Stash file" pill to video cards imported from Stash
0.82 moved custom yt-dlp arguments button to the left of Settings
0.81 yt-dlp nightly update pip fallback
0.80 yt-dlp update to nightly channel
0.77 migrated modular
0.76 Stash file import export modular
0.75 central library data
0.74 multiselect
0.73 reencode move prompt
0.72 history lookup cors
0.70 queue stats
0.69 reencode menu
0.68 m3u edit confirm(1)
0.67 m3u auto submit(1)
0.66 m3u retry flow
0.65 m3u autofallback
0.65 ledger layout
0.64 ui tweaks
0.63 wider desktop display
0.62 stash sync fix
0.61 stash integration
0.60 medium width only
0.59 wide desktop layout
0.58 mobile ledger card
0.56 thumb below icons
0.55 thumb overlay icons
0.54 icon row tabler
0.52 persistent download prefs
0.51 hide completed filter
0.50 encoder estimate resolution fix
0.49 CRF File size estimate bug fix
0.48  AMF encoding crash fix
0.47 libx265 odd width fix
0.46 ledger probe retry fix
0.45 manual estimate refresh
0.44 ytdlp update check
0.43 card path
0.42 one line
0.41 copy while downloading
0.40 persistent failures
0.39 retry download
0.38 search history mode
0.37 search history mode
0.36 display
0.35 option icon
0.34 Encoding odd resolution width bug fix
0.34 audio sync
0.33 minor fixes
0.32 download info
0.31 encode manager
0.30 encode manager
0.29 Menu revamp 2
0.28 Menu revamp
0.27 external programs
0.26 restart fix
0.25 EXE lan
0.24 Fixed queued reload bug
0.23 open filelocation
0.22 EXE Builder
0.21 UI Tweaks
0.19 remove saved folders
0.18 right click bug on ipad
0.16 Delete file bug fixed
0.15 Playback position memory
0.14 UI tweaks
0.12 Free Space
0.12 download button tweaks
0.11 TArget folder
0.09 Filter sort controls