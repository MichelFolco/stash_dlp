# Stash_DLP

Self-hosted web app to downlaod most videos on the internet. Essentially a frontend for yt-dlp.

## Workflow
- Set a download folder and the resolution wanted (right-click on logo for option menu)
- Paste the link of a video to download (supported sites: https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)
- Edit the fetched title if needed and press enter to download or press escape to cancel the download sequence.
- If the download fails to start (unsupported site), you can try again after activating the "M3U sniffer" method in the right-click menu.
- Click on the thumbnail to play video, click on the video card outside of the thumbnail to display options. 
	- Extract Audio:  Create an mp3 from the audio of the video
	- Delete, rename, copy link or file name, move the file to the target folder (set the target folder on logo right click option menu)
- You can search the download history by toggling the looking glass icon and pasting a file name (partial names accepted).  Returns the download link. 	

## How To Run

- Start StashDLP.exe
- **http://127.0.0.1:8722** in a browser or click the icon on the system tray. 

## Build EXE

-Run build_exe.bat, the file will be compiled in the dist sub folder.

-Copy `icon.ico` into the same folder as the built `dist\StashDLP.exe` -
that's where the tray icon looks for it at runtime.

## File layout

Everything the app generates lives in
a `stash_dlp_data/` subfolder of the selected download folder., kept separate from the actual
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

Thumbnails are named - `<filename>.jpg`


## Exposing over Tailscale / your LAN

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
  `STASH_DLP_PORT`

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

### Launching at Windows boot

Two options, roughly in order of simplicity:

**1. Startup folder (simplest)**
- Press `Win+R`, type `shell:startup`, hit Enter
- Right-click inside the folder → New → Shortcut
- Target: "C:\path\to\stash_dlp_web\dist\stashSLP.exe"`
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

