@echo off
REM Sets STASH_DLP_HOST for this process only (doesn't touch your
REM system-wide environment) and launches the tray app with no console
REM window. This launcher also watches project source files and automatically
REM restarts the server after changes are detected.
REM Point your Startup-folder shortcut or Task Scheduler action at THIS file.

set STASH_DLP_HOST=0.0.0.0

REM Uncomment and edit if you want downloads to land somewhere specific:
REM set STASH_DLP_SAVE_DIR=C:\path\to\downloads

REM Uncomment and edit if 8722 is taken by something else:
REM set STASH_DLP_PORT=8722

REM Start the watcher hidden. It starts tray_launcher.py, monitors project
REM files, and restarts it after source changes. Changes are debounced so
REM saving several files at once causes only one restart.
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0watch_and_restart.ps1"
