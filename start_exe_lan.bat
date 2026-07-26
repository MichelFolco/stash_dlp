@echo off
REM Same idea as start_tray_lan.bat, but for the standalone StashDLP.exe
REM build instead of running tray_launcher.py via pythonw.exe. Sets
REM STASH_DLP_HOST for this process only (doesn't touch your system-wide
REM environment) and launches the exe. Point your Startup-folder
REM shortcut or Task Scheduler action at THIS file instead of the exe
REM directly if you want it reachable from your phone/other devices.

set STASH_DLP_HOST=0.0.0.0

REM Uncomment and edit if you want downloads to land somewhere specific:
REM set STASH_DLP_SAVE_DIR=C:\path\to\downloads

REM Uncomment and edit if 8722 is taken by something else:
REM set STASH_DLP_PORT=8722

start "" "%~dp0StashDLP.exe"
