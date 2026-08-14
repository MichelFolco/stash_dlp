# Changelog
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