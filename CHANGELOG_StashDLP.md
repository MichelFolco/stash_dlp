## v1.28 - Title prefix
- Added a "Title Prefix" text field + "Apply Title Prefix" toggle to the settings flyout. When on, the saved text is prepended to every generated title - single downloads, M3U8 stream sniffs, and playlist entries alike (the same three places the existing [domain] tag already applies to) - so it shows up already in place during the title review/edit step.
- The prefix text and its on/off toggle are stored separately, so turning it off doesn't erase what you typed - flip it back on later and it's still there.

## v1.27.1 - Fixed playback crash on filenames with emoji/non-Latin-1 characters
- `/api/jobs/stream` was setting a raw (non-percent-encoded) `X-Media-Filename` response header. HTTP headers must be Latin-1-encodable, so any file whose title contained an emoji or other non-Latin-1 character (common on unedited playlist titles) crashed the endpoint with a 500 and refused to play in-app, even though the file itself was completely fine (playable in VLC, valid per ffprobe).
- The header value is now percent-encoded on the way out and decoded on the way in (only consumer: the audio-sync tool's filename display) - round-trips exactly, no filenames or downloaded files need to change.

## v1.27 - Nested, uncapped download/target folders
- Replaced the old capped (8-entry) recent-folders list with an uncapped list of saved "root" folders for both the download and target folder pickers.
- The DL:/Target: quick-select dropdown now shows each saved root's actual on-disk subfolders nested underneath it (unlimited depth, 5-level safety cap), live-scanned each time the dropdown opens. Click a folder's chevron to expand its children; click the folder name itself to switch to it.
- Adding a root that's already inside another saved root is rejected; adding a root that's an ancestor of existing saved roots absorbs them instead of keeping redundant narrower entries.
- Folders (root or nested) are ordered most-recently-used first, tracked per exact path - picking a nested subfolder now also bumps its root to the top of the root list.
- The "Change Download/Target Folder..." modal now manages the flat list of saved roots only (add via Browse/Set Folder, remove via the ✕) - nesting is shown in the quick dropdown, not the modal.
- Saved roots that no longer exist on disk are now dropped automatically instead of just being hidden from the list forever.
- Existing recent-folders lists are migrated automatically to the new root-folder format on first launch after updating.

## v1.26.5 - Playlist title numbering format
- Updated optional playlist title numbering to use zero-padded two-digit numbers (`01`, `02`, `03`, etc.) instead of `1`, `2`, `3`.
- Updated the playlist numbering confirmation prompt to show the new format.

## v1.26.4 - Playlist title numbering
- When starting a playlist download, StashDLP asks whether playlist item titles should be numbered.
- When enabled, filenames are prefixed with their playlist position (for example `1-My Video`, `2-Another Video`).
- Numbering is applied to the queued playlist items on the server, preserving the playlist order.

## v1.26.3
- Playlist downloads now snapshot the download folder when the playlist is queued. Changing the app's download folder while the playlist is still downloading no longer redirects remaining playlist items to the new folder.

## v1.26.2 - Queue auto-play

- Added automatic playback of the next item in the queue when the current item finishes.
- Playback follows the queue's current sort/filter order.
- The next item is determined before the completed item is hidden by the Hide Completed filter.

## v1.26.1 - Management menu styling fixes

- Restored compact styled rows and Edit controls in External Programs.
- Restored compact styled rows and Edit controls in yt-dlp Arguments site rules.
- Restored Check Tag recent-tag chips and Stash scene result rows.
- Restored consistent Open/Import controls for Check Tag and Largest Files results.
- Added empty-state styling for management lists.

## v1.26 - UI Fixes and Navigation

- Restored styled `.btn` controls used by dialogs and the Encoding Queue.
- Restored compact styling for ledger status/badge pills.
- Restored formatting for the Re-encoded version found dialog and its comparison cards.
- Hamburger navigation is now a true toggle; clicking elsewhere no longer auto-closes it.
- Moved Download and Target folder displays into the hamburger navigation panel.

## v1.25.1 - Clipboard Monitoring Fix

- Fixed Windows clipboard URL detection on 64-bit systems by declaring the Win32 clipboard/pointer APIs with the correct ctypes pointer types.
- Added short retries when another application temporarily owns the clipboard immediately after a copy operation.
- Clipboard-triggered downloads no longer require the StashDLP input state to be READY; a newly copied valid URL takes priority and starts the automatic download pipeline.

# v1.25 - Clipboard Monitoring

- Added optional Windows clipboard monitoring for HTTP(S) URLs.
- Monitoring works regardless of which application is focused.
- Newly copied URLs automatically start downloads without title staging/editing.
- Added a Settings toggle for Clipboard Monitoring.
- StashDLP itself is excluded as the active source to avoid re-downloading links copied by the app.

# v1.24.1
- Removed remaining intrinsic minimum-width constraints from the ledger toolbar and its filter/sort controls so the desktop window can actually be resized down to the ultra-narrow breakpoint.

# v1.24
- Added an ultra-narrow display mode for panes as narrow as a quarter of a mobile screen.
- Ultra-narrow view keeps only the URL input, Transfer All to Target arrow, and compact download ledger thumbnails.
- Download cards show live progress, speed, and ETA while hiding titles and secondary metadata.
- Preserved card context-menu behavior and direct playback for completed thumbnails.
- Removed the application's global minimum width so the window can shrink below the previous 300px limit.

# v1.23
- Revamped the UI with a narrower, more professional dark design while preserving the existing workflow.
- Added a persistent **Download** button beside the URL field; the URL input, logo, and download action remain visible together.
- Replaced the icon-only view switcher with explicit **Downloads**, **Encoding Queue**, and **History** navigation.
- Made **Download Folder** and **Target Folder** information permanently visible with compact paths and folder icons.
- Replaced emoji controls with consistent Tabler icons and modern rounded controls.

# v1.22
- Added **Largest Files** to the Stash menu. It lists the 50 largest Stash scene files by size, descending, using the same Open/Import results modal as Check Tag. File sizes are fetched in one GraphQL request and sorted locally for Stash-version compatibility.
- Added Windows clipboard monitoring: when a supported browser is focused and Stash DLP is not focused, newly copied HTTP/HTTPS URLs are automatically placed into the Download URL field.

<!--
MAINTENANCE (for any LLM/agent editing this project): adding an entry
here is a two-file edit. backend/config.py's APP_VERSION constant
(shown to the user when they click the logo) must be bumped to match
the version number in the new top entry below, in the same turn. This
has drifted out of sync before - don't add an entry here without also
updating config.py.
-->

## v1.21 - Hide empty playback progress bars
- Completed cards no longer show an empty playback progress bar when the saved playback position is `0` or `null`.
- The full watched bar remains visible for files marked `fully_played`.

## v1.20 - Rename Converted/ twin with source file
- When a completed card has a twin in `Converted/`, renaming the card now renames the twin to the same filename stem while preserving the twin file extension.
- The rename is rejected if the new twin filename would collide with an existing file in `Converted/`.

## v1.19 - Replace completed downloads with their Converted/ twin
- Added **Replace with Twin** to completed cards that have a twin in `Converted/`.
- The action moves the twin into the download folder, replacing the original file, and refreshes the card metadata.

## v1.18 - Per-file playback progress bar, sticky "watched" flag, compact folder paths
- Every completed file's card now shows a thin playback-progress bar (amber) reflecting `playback_position`/`duration`, so scanning the ledger shows at a glance what's been started vs. never touched - previously this data existed (used for player resume) but was never surfaced outside the player itself.
- New sticky `fully_played` flag, separate from `playback_position`: reaching the end of a file already reset `playback_position` back to 0 (so the next play starts over rather than resuming right at the end) - without a separate flag, a finished file and a never-started one were indistinguishable once position was back to 0. `fully_played` only ever flips False→True (set via a new `completed` param on `POST /api/jobs/playback-position`, sent when playback reaches the end) and is never cleared afterward, even by a full rewatch - a rewatch just updates `playback_position` normally on top of it. Surfaced as a green "✓ WATCHED"/"✓ LISTENED" badge next to the filename, and the progress bar itself renders as a full green bar (rather than misleadingly empty) when `fully_played` is set and no rewatch is currently in progress.
- New `compactFolderDisplay()` helper collapses long folder paths to `drive/.../last-folder (free space)` (e.g. `C:\Users\Phil\Documents\GitHub\project\downloads` → `C:\...\downloads (123.3 GB free)`) instead of letting the browser's CSS ellipsis clip whichever end happens to overflow first, which tended to hide the one thing that actually identifies the folder day-to-day. Applied to the settings-menu Folder/Target rows, the toolbar DL:/Target: chips, and every job card's per-file path line. The full uncollapsed path is still always available via the existing tooltip/title attribute.
- `fully_played` threaded through every job-dict construction site (`start_job`, playlist batch items, extracted-audio jobs, Stash-imported jobs, and both the in-memory and on-disk filesystem-scan reconciliation paths) alongside the existing `playback_position` field, so it survives a refresh/restart the same way.

## v1.17 - Widened auto-refresh interval, denylist instead of allowlist
- Auto-refresh poll interval widened from 5s to 30s (matching the existing Stash-status poll) - everything the app does to its own jobs already arrives instantly over the websocket, so this poll's only real job is catching external changes (a file dropped into Converted/ by hand), which was never on a tight SLA. Also cuts the frequency of the full `os.listdir()` + `Converted/` listing + potential ffprobe calls each tick makes, which isn't free on a networked download folder.
- `job_manager.seed_from_filesystem()`'s replace-rebuild and `filesystem_scan.scan_filesystem()`'s queue.json rewrite were both allowlists (only kept status X across a refresh) - the exact shape of bug that let QUEUED get silently wiped in v1.15 until the v1.16 hotfix. Both are now denylists instead: they only drop the specific statuses they're actually authoritative over (`DONE`/`ERROR`/`CANCELLED` derived from what's really on disk), and preserve everything else by default - so a future status won't need a matching edit in these two places just to avoid being silently dropped on the next refresh.

## v1.16 - Fixed playlist queue vanishing after the 5s auto-refresh
- Bug: right after confirming a playlist download, all queued entries (e.g. all 26 for a 26-video playlist) would appear in the ledger for well under a second, then vanish entirely except the 3 that had already started downloading.
- Root cause: `job_manager.seed_from_filesystem(replace=True)` - called on every folder change *and* on every periodic `/api/refresh` (the frontend's 5-second auto-refresh poll, which predates playlist support) - only preserved jobs with status `DOWNLOADING` across the rebuild. The new `QUEUED` status introduced in v1.15 wasn't in that allowlist, so the very next auto-refresh tick after queueing a playlist wiped every item still waiting on a download slot.
- Fixed in two places: `job_manager.seed_from_filesystem()`'s in-memory rebuild now keeps `QUEUED` alongside `DOWNLOADING`; `filesystem_scan.scan_filesystem()`'s disk-persisted `queue.json` rewrite now does the same (this second one didn't cause the visual bug, but would have silently dropped queued playlist items from disk permanently on a server restart mid-playlist).
- Also tightened `start_playlist_batch()`'s duplicate-title dedupe check to skip `QUEUED` as well as `DOWNLOADING` (previously only guarded against re-queueing something already downloading).

## v1.15 - Playlist download support
- Every pasted URL is now checked with a new `probe_playlist()` (`ytdlp_utils.py`) - a `yt-dlp --flat-playlist --dump-single-json` call that lists a playlist/channel's entries without downloading anything, run in parallel with the normal single-item fetch-title/M3U-sniff call so a regular single-video paste pays no added latency. A result of 2+ entries is treated as a playlist; anything else (single video, error, timeout) falls through to the existing single-item pipeline unchanged.
- Detected playlists skip the per-item EDITING step entirely and are queued as a batch via new `POST /api/playlist/probe` + `POST /api/playlist/queue` endpoints and `job_manager.start_playlist_batch()`. Each entry lands in the ledger immediately with a new `QUEUED` status, then downloads at most 3 at a time (`PLAYLIST_CONCURRENCY`) via an `asyncio.Semaphore` scoped to that one batch - a second playlist queued separately gets its own independent semaphore/cap, so two playlists running at once means up to 6 concurrent downloads total, not a shared cap of 3.
- New `QUEUED` job status is fully wired through the ledger: its own card style (waiting-for-slot message, no progress bar), Cancel available while waiting (flips it straight to `CANCELLED` rather than waiting out the queue), excluded from multi-select/batch-delete the same way `DOWNLOADING` already was, and a new `job_status` websocket message for the QUEUED→DOWNLOADING transition (distinct from `job_finished`, which still only fires on a terminal state).
- New **Auto-Confirm Titles** setting (`auto_confirm_titles` in download prefs, off by default) - when on, a fetched/sniffed/playlist title is submitted as a download immediately instead of staged in the input box for review. Applies uniformly to single downloads, M3U-sniffed links, and playlist entries alike, rather than being a playlist-only special case.
- `job_manager.cancel_job()` is now async (was sync) to support broadcasting the QUEUED→CANCELLED transition over the websocket; its one call site in `main.py`'s `/api/jobs/cancel` was updated to `await` it.
- A detected playlist now confirms before queueing (`window.confirm`, matching every other destructive/bulk action in the app) - shows the entry count and playlist title where available, with a chance to back out before anything is queued. Declining resets to the ready state, same as any other cancelled paste.

## v1.14 - Fixed Synchronize Audio final render not matching the previewed delay
- `createClip()` (frontend) was hardcoding `delay_ms: 0` on every Create Clip call, including after **Redo Clip** mid-session - silently discarding whatever delay the user had already dialed in and confirmed sounded right, and resetting the visible delay field back to 0 with it. This contradicted `create_clip()`'s own backend contract (it accepts `delay_ms` specifically so a Redo Clip -> Create Clip keeps the dialed-in delay), which the frontend never honored. Now `createClip()` sends the delay currently in the field and leaves the field alone instead of zeroing it.
- Separately, **Confirm Sync** was reading the delay input's live value directly, with no guarantee it had ever actually been rendered into the clip the user just previewed - a dial-button nudge or manual edit made after the last "Apply Sync" click could flow straight into the full-video render unpreviewed. `syncAudioState` now tracks `appliedDelayMs` (the delay actually baked into whatever's currently playing, set by `createClip()`/`applyClipDelay()`), and Confirm Sync refuses to proceed if the field has drifted from it, prompting the user to Apply Sync first instead of silently rendering an unverified value.
- Together these were the root cause of full syncs occasionally not applying the delay the user had actually decided on - the underlying ffmpeg delay-application logic (`_run_delay_render`) itself was already correct and unchanged.

## v1.13 - Fixed stale app version display
- `APP_VERSION` in `config.py` had drifted to "1.10" while this changelog was already at v1.12 (the logo-click version display reads directly from that constant, so it was showing two versions behind). Bumped to match, and added explicit maintenance notes in both this file and `config.py` telling any future LLM/agent that a new changelog entry and the `APP_VERSION` bump must happen together.

## v1.12 - History-search API now checks every download folder
- `storage.search_history()` (backing `/api/history-search`, used for "copy link" fallback and Search History Mode's history-fill) no longer only reads the currently active download folder's log. New `settings.get_all_history_log_paths()` lists every `downloads_history.log` under the central `LIBRARY_DATA_DIR` store - one per download folder this app has ever been pointed at - with the current folder's log checked first, then the rest. A file downloaded under a folder that isn't the one currently open now still resolves to its URL instead of coming up empty.

## v1.11 - Renames now logged to the history log
- `job_manager.rename_job()` now calls `write_to_history_log()` after a successful rename, writing a `RENAMED from <old filename>` entry (using the job's URL if one is on record) so the history log reflects the file under its new name instead of going stale/orphaned after a rename.

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