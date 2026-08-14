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