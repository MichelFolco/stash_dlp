# Stash DLP — Web Port

A browser/localhost port of `stash_dlp.py`. FastAPI backend + WebSocket for
live progress, vanilla HTML/CSS/JS frontend styled to match the original
PyQt6 ledger.

## Requirements

- Python 3.10+
- `yt-dlp` on your PATH
- (Optional, for the thumbnail fallback when yt-dlp doesn't grab one -
  e.g. M3U-sniffed streams, or files discovered on disk before this
  feature existed) `ffmpeg` on your PATH
- (Optional, for M3U Sniffer Mode) `playwright` + a Chromium install:
  ```
  pip install playwright
  playwright install chromium
  ```

## Setup & run

```bash
cd stash_dlp_web
pip install -r requirements.txt
python backend/main.py
```

Then open **http://127.0.0.1:8722** in a browser. Downloads land in the
project root by default the first time you run it — set
`STASH_DLP_SAVE_DIR` to change that initial default, or (easier) change
it any time from the app itself: right-click the logo → **Change
Download Folder...**. That opens a small dialog with:
- a text field you can type/paste a path into
- a **Browse...** button that pops a native OS folder picker *on the
  machine running the backend* (needs `tkinter`, which ships with the
  standard python.org Windows installer already - nothing extra to
  install). This only works when you're browsing from the same machine
  as the server; if you're reaching the app remotely (e.g. from your
  phone over Tailscale), it'll tell you so instead of popping a dialog
  on your PC that you can't see.
- a list of recently-used folders to jump back to with one click

The chosen folder is remembered in `_app_settings.json` next to
`backend/`, along with the recent-folders list (last 8, folders that no
longer exist are filtered out automatically). `queue.json`/the history
log travel with whichever folder is currently active, so each folder
keeps its own self-contained state — same as the desktop app's
single-folder model.

## What's ported 1:1

- Job pipeline: paste/clipboard → title fetch → editable filename prompt
  → download with live progress → DONE/CANCELLED/ERROR ledger card
- File size on completed cards
- Resolution cap (Best/720p/480p), domain tagging toggle
- M3U Sniffer mode (playwright-based), Find Link / history-search mode
- Logo right-click menu (yt-dlp version + "just updated" flag, Max Res,
  Tag toggle, M3U toggle, Change Download Folder)
- Refresh (re-scans the download folder - useful if files changed on
  disk outside the app), keyboard shortcuts (Ctrl+F/D/M, Ctrl +/-,
  Enter, Esc)
- Clicking the Download mode button (⬇️) while a valid URL is already
  sitting in the input box - e.g. one you just pasted, or one Find Link
  mode found via history search - starts the download pipeline directly
  with that URL (title fetch → confirm/edit → Enter to submit), instead
  of just resetting the box. Falls back to the normal reset behavior if
  the box is empty or doesn't contain a URL. Tested directly: URL typed
  into the box, garbage text (correctly ignored), and the specific
  Find-Link-mode-to-Download-mode handoff.
- Mini mode (compact layout, live aggregate speed/ETA)
- A filter box + sort controls (Date Added / File Size / Filename, with
  a direction toggle) sit right above the ledger - entirely client-side,
  no backend round-trip, so it stays responsive as you type and composes
  correctly with whatever's currently downloading or being filtered.

## Where the web version diverges on purpose

Ledger item interaction isn't a 1:1 port - it's simpler than the desktop
app by design:

- **Thumbnail** on every completed card (yt-dlp's own thumbnail if it
  grabbed one, otherwise an `ffmpeg` frame-grab a few seconds in, cached
  next to the video as `<name>.thumb.jpg`)
- **Left-click any card** opens one menu instead of the old
  left-click-to-copy / right-click-for-actions split:
  - While downloading: **Cancel Download**
  - Once finished (video only): **Play Video** (in-app), **Extract
    Audio** (rips the audio track into its own file/ledger entry,
    leaving the source video untouched)
  - Once finished (audio only): **Play Audio** (in-app)
  - Once finished (any type): **Delete File** (confirms first),
    **Rename File**, **Copy Link**, **Copy File Name**
- There's no "Open in Explorer" - dropped to keep the menu focused

## Audio

- **"Audio Only" quality option** (right-click the logo → Max Res)
  downloads just the audio track as an mp3, no video at all
- **Extract Audio** on any completed video pulls its audio into a new
  `<name> (Audio).mp3` file and adds it to the ledger as its own entry -
  the original video is untouched
- **Play Audio** streams mp3/m4a/etc. in-app through the same modal used
  for video (same `Range`-request-backed endpoint, just an `<audio>`
  element instead of `<video>` when the item is audio-only) - tested
  directly: correct duration loads, playback advances, seeking works,
  and closing the modal releases the stream. Confirmed video playback
  itself didn't regress from sharing the same modal.
- Any audio file already in the download folder - one you extracted,
  downloaded, or just copied in by hand - gets picked up on boot/refresh
  and clearly marked: a purple "AUDIO" badge next to the title, a music
  icon instead of a video thumbnail, and a distinct tint on the card.
  Recognized extensions: mp3, m4a, opus, aac, flac, wav, ogg.
- Tested end-to-end: a real audio-only download, extracting audio from a
  real video (original file left alone, new entry correctly labeled),
  and boot-time detection of a mixed folder (one audio + one video file,
  no prior tracking data) correctly identifying each.

In-app playback streams over HTTP with proper `Range` request support
(206 Partial Content, correct `Content-Range`/`Accept-Ranges` headers),
which is what actually matters for phone compatibility - mobile Safari
in particular refuses to play video at all without it. Tested directly:
correct headers/byte-ranges for full requests, small probe ranges,
mid-file seeks, open-ended ranges, and out-of-bounds/missing-file error
cases, plus a real end-to-end browser pass (metadata loads, playback
advances, seeking actually jumps via a new Range request). This works
the same over Tailscale as the rest of the app.

## Free disk space

Both the download folder and target folder lines in the logo menu show
free space for that drive, e.g. `Folder: C:\Videos (C: 123.3 GB free)`.
It refreshes every time you open the menu (not just once at boot), so
switching folders or freeing up space elsewhere shows up next time you
check. On Windows this includes the drive letter; on Linux/macOS (no
drive letters) it just shows the size. Verified directly against `df -h`
for accuracy, and confirmed the menu re-fetches live rather than
showing a stale value from page load.

## Clipboard auto-scan removed

The app no longer reads the clipboard at all. Paste (or type) a link
into the input field manually and press Enter - it only ever acts on
what's actually in the box. If the box is empty or doesn't contain a
valid URL, it just prompts you to paste one; it doesn't fall back to
checking the clipboard anymore. Default messages throughout the app
("Paste a link, then press ENTER...", etc.) were updated to match.
Verified directly: with a URL sitting on the clipboard but the input
box empty, pressing Enter does nothing with it - only manually entering
a URL and pressing Enter starts anything, and it's that manually-entered
URL that gets downloaded.

## Thumbnail click plays directly

On a completed card, clicking the thumbnail itself starts playback
right away (video or audio, whichever the item is) - no menu detour.
It shows a ▶ overlay on hover so it's discoverable. Clicking anywhere
else on the card (title, size, badge) still opens the options menu as
before. For anything not yet completed (downloading, error, cancelled)
there's nothing to play, so a thumbnail click there falls through to
the normal menu instead. Verified all three cases directly in a
browser: thumbnail click on a done item opens the player (not the
menu), clicking the title on that same card opens the menu (not the
player), and a thumbnail click on an errored item correctly falls back
to the menu.

## Remove entries from recent folders

Both folder-picker modals (download folder and target folder) now show
an "✕" next to each recent entry - click it to drop just that one from
history. Doesn't touch the folder itself, just the remembered list, and
persists immediately. Verified for both modals directly: removing one
entry leaves the others in place, the modal stays open so you can clean
up several in a row, and the change is confirmed server-side afterward.

## Restart App

Right-click the logo → **Restart App** (warns first if anything's
actively downloading). It relaunches the same way it was started -
whether that's `python backend/main.py` directly or via
`tray_launcher.py` - so the tray icon comes back too if that's how you
run it.

Under the hood: a tiny detached helper process waits ~1.5s then starts
the app fresh, while the current process exits almost immediately
first. That ordering matters - it avoids a port-binding race where the
new instance tries to grab the port before the old one has actually
released it. The exit itself isn't a graceful shutdown (no time to
finish in-flight requests), which is why it warns about interrupting
active downloads first.

The page recovers on its own - no manual refresh needed. The existing
WebSocket auto-reconnect logic now also triggers a full re-sync (jobs,
folders, version) on every reconnect, not just a bare socket
reconnection, so the ledger catches up correctly once the new process
is back up.

**Tested directly, not just wired up**: triggered a real restart via
the API and confirmed with `ps`/PID checks that the old process
actually exited and a genuinely new one came up on the same port,
correctly re-scanning the folder from scratch. Then did the same thing
through an actual browser session end-to-end - clicked Restart App,
confirmed the dialog, watched the input field disable with a status
message, and confirmed it automatically re-enabled and the ledger
re-populated once the new instance was ready, with no page reload.

One known rough edge: since the exit is forced rather than graceful,
if you're running via the tray icon, the icon itself doesn't get a
chance to clean up before the process dies - Windows usually clears a
stale tray icon on the next hover/click, but you might see a ghost icon
briefly until then.

## Fixed: files staying locked after playback (Windows)

**The bug**: after playing a file and stopping, Windows would refuse to
let you delete/move/rename it ("in use by python") until the whole app
was closed.

**Root cause**: the video/audio streaming endpoint used plain
synchronous generators for reading file chunks. Starlette runs those in
a background thread pool rather than as part of the request's own
async task - so when a connection ends abruptly (e.g. you stop
playback and the browser aborts an in-flight Range request), cancelling
that background thread promptly isn't reliable. The file could stay
open in that thread well after the request was actually over. Linux
doesn't surface this as a user-visible problem (you can delete a file
that's still open), which is exactly why it went unnoticed until
tested on Windows, where an open handle actively blocks the file.

**Fix**: switched to async generators with an explicit `try/finally`
around the file handle. Starlette can cleanly cancel an async generator
the instant a connection ends, which runs the `finally` and closes the
file immediately - no more waiting for a background thread to notice.

**Verified with a real A/B comparison**, not just a code read: wrote a
raw client that opens a stream, reads a small chunk, then aborts the
connection outright (exactly what happens when you stop playback) and
checked the server's actual open file descriptors afterward.
- Old implementation: the file descriptor stayed open indefinitely
  (confirmed still open several seconds later)
- New implementation: released immediately on the same abrupt disconnect

Also re-ran the full existing Range-request test suite (byte-exact
mid-file seeks, probe ranges, open-ended ranges, out-of-bounds/missing
file errors) plus real browser playback to confirm nothing regressed.

## No more flashing console windows (Windows)

Every `yt-dlp`/`ffmpeg` subprocess spawn (downloads, title fetch, version
check, thumbnail extraction, audio extraction) now passes
`creationflags=subprocess.CREATE_NO_WINDOW` on Windows, so they run
invisibly instead of briefly flashing their own console window in front
of everything. This is a no-op on Linux/macOS (the flag doesn't exist
there), verified the full pipeline (download, thumbnail fallback,
extract audio) still works correctly with it in place.

## Resumable playback

Play Video/Play Audio remember where you left off, stored server-side
in `queue.json` rather than the browser - so it survives a browser
crash/close *and* the app itself restarting, not just a page reload.

- Position saves periodically during playback (every ~5s), immediately
  on pause, and immediately when you close the player
- Reopening a file picks up right where you left off
- If you were within 5 seconds of the end when you stopped, or the file
  played all the way through, it resets to 0 instead of resuming into
  the credits or replaying nothing
- Tested directly end-to-end: played a real video, seeked partway
  through, closed the player, then restarted the entire server process
  and opened a brand-new browser session (not just a reload) - it
  resumed at the correct timestamp. Also verified the near-end and
  fully-watched cases both correctly reset to 0.

## Move to Target

A second, persistent folder you move finished files to - separate from
the download folder, and global rather than tied to whichever download
folder is currently active (so it stays put even if you switch download
folders).

- **Set it**: right-click the logo → **Change Target Folder...** - same
  modal as the download folder (type a path, Browse... for a native
  folder picker, or pick from recent target folders). Persists in
  `_app_settings.json`, survives a restart.
- **Move one file**: left-click any completed item → **Move to Target**
- **Move everything**: **Move All to Target** button next to Refresh -
  confirms first, then moves every completed item in one go and
  reports which (if any) failed and why
- Moving a file also cleans up its now-orphaned thumbnail from the
  source folder's `stash_dlp_data/.thumbnails/`. The target folder isn't
  tracked by the app - it's just a destination, not a second library.
- Tested directly: single move, batch move-all, settings surviving an
  actual server restart, and error handling for both "no target folder
  configured yet" and "a file with that name already exists at the
  destination" (rejected cleanly, source file untouched either way).

## File layout

Everything the app generates for the current download folder lives in
a `stash_dlp_data/` subfolder of it, kept separate from your actual
media files:

```
<your download folder>/
  Some Video.mp4
  Some Song.mp3
  stash_dlp_data/
    _download_queue.json
    downloads_history.log
    .thumbnails/
      Some Video.jpg
      Some Song.jpg
```

Thumbnails are named plainly - `<filename>.jpg`, no `.thumb` suffix -
since they're already isolated from the media files and there's no
longer a naming collision to avoid.

**Existing folders migrate automatically.** If a folder still has
`_download_queue.json`/the history log/thumbnails sitting loose at the
root from before this existed, the app moves them into `stash_dlp_data/`
the first time it scans that folder (on boot, refresh, or switching to
it). This is safe to happen more than once - already-migrated folders
are a no-op. Tested directly: a simulated legacy folder (loose
queue.json, history log, and both old thumbnail naming styles) migrated
correctly with all data intact, running the migration twice was a clean
no-op, and delete/rename correctly followed files into the new layout
afterward.

## Known differences from the desktop app

- **Clipboard scanning**: browsers require a user gesture before reading
  the clipboard. Pressing Enter still works (it's a real keypress), but
  there's no passive background polling.
- **Mini mode** collapses the layout instead of resizing/repositioning an
  actual OS window (browsers can't reposition their own window from JS
  in most cases).
- **Delete/Rename/thumbnail generation** run on the *server* machine's
  filesystem - fine for the normal case (backend on your own PC), but if
  you're reaching the app from another device, these still act on the
  server's files, which is what you want.

## Exposing it over Tailscale / your LAN

By default the server only listens on `127.0.0.1` (your PC only). To
reach it from your phone or another device, it needs to bind to all
interfaces instead, via the `STASH_DLP_HOST=0.0.0.0` environment
variable.

**Easiest: use `start_tray_lan.bat`** — it sets the variable for just
that one launch (doesn't touch your system-wide environment at all) and
starts the tray app silently:

- Point your Startup-folder shortcut or Task Scheduler action at
  `start_tray_lan.bat` instead of `tray_launcher.py` directly
- Target: `C:\path\to\stash_dlp_web\start_tray_lan.bat`
- Edit the commented-out lines inside it if you also want a custom
  `STASH_DLP_SAVE_DIR` or `STASH_DLP_PORT`

**Alternative: a permanent user environment variable**, if you'd rather
set it once system-wide:
1. Press `Win`, type `env`, open "Edit environment variables for your account"
2. Under "User variables", click New
3. Variable name: `STASH_DLP_HOST`, Value: `0.0.0.0`
4. OK out of both dialogs
5. Important: this only takes effect for *new* processes started after
   you set it — restart any shortcut/scheduled task, or reboot, before
   testing

**Either way, also check Windows Firewall.** The first time the app
listens on `0.0.0.0`, Windows should prompt you to allow it through the
firewall — allow it for **Private networks** (Tailscale traffic
typically shows up as Private). If you don't get a prompt, go to
Windows Defender Firewall → "Allow an app through firewall" and make
sure `python.exe`/`pythonw.exe` (or specifically port 8722) is allowed
for Private networks.

Then from your phone: `http://<your-PC's-Tailscale-IP>:8722`.

## Not yet tested end-to-end

I ported and syntax-checked all of this, but couldn't run a live
`yt-dlp` download or a real Playwright browser session in this
environment. Test the core loop first (paste a URL → download a short
clip) before relying on it for anything important, and let me know what
breaks.

## Running in the system tray (Windows)

`tray_launcher.py` wraps the server in a background thread and shows a
tray icon with **Open Stash DLP** / **Quit** — same `pystray` pattern as
YTMusicWeb.

```bash
pip install pystray Pillow
```

Then run it with `pythonw.exe` (not `python.exe`) so no console window
appears:

```
pythonw.exe C:\path\to\stash_dlp_web\tray_launcher.py
```

**Icon**: `icon.ico` (multi-resolution, for crisp taskbar/tray rendering
at any DPI) is included, generated from your `>stash_dlp` logo - the
chevron mark on a dark rounded square with a cyan border, matching the
app's own theme. `icon.png` is included too as a fallback. The full
wordmark logo also now appears in the app's own top bar
(`static/logo.png`) and as the browser tab favicon
(`static/favicon.png`).

### Launching at Windows boot

Two options, roughly in order of simplicity:

**1. Startup folder (simplest)**
- Press `Win+R`, type `shell:startup`, hit Enter
- Right-click inside the folder → New → Shortcut
- Target: `pythonw.exe "C:\path\to\stash_dlp_web\tray_launcher.py"`
- This runs it whenever you log in, no console window

**2. Task Scheduler (more control — can restart on failure, run before
login, etc.)**
- Open Task Scheduler → Create Task (not "Basic Task", for more options)
- General tab: check "Run whether user is logged on or not" if you want
  it available even without an interactive session
- Triggers tab: New → "At log on"
- Actions tab: New → Program: `pythonw.exe`, Arguments:
  `"C:\path\to\stash_dlp_web\tray_launcher.py"`
- Settings tab: consider "Restart the task if it fails"

Either way, `yt-dlp` still needs to be reachable on `PATH` for whatever
account the task/shortcut runs under.

