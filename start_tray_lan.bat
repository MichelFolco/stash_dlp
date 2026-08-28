@echo off
REM Sets STASH_DLP_HOST for this process only (doesn't touch your
REM system-wide environment) and launches the tray app with no console
REM window. Point your Startup-folder shortcut or Task Scheduler action
REM at THIS file instead of tray_launcher.py directly.

set STASH_DLP_HOST=0.0.0.0

REM Uncomment and edit if you want downloads to land somewhere specific:
REM set STASH_DLP_SAVE_DIR=C:\path\to\downloads

REM Uncomment and edit if 8722 is taken by something else:
REM set STASH_DLP_PORT=8722

start "" python.exe "%~dp0tray_launcher.py"
