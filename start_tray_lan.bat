@echo off
rem Starts StashDLP in the system tray, listening on the LAN (0.0.0.0): no console window, opens the app in the browser when ready.
rem Uses .venv if there is one; otherwise the Python on PATH.
cd /d "%~dp0"
set STASH_DLP_HOST=0.0.0.0
if exist ".venv\Scripts\pythonw.exe" (
  set "PATH=%~dp0.venv\Scripts;%PATH%"
  start "" ".venv\Scripts\pythonw.exe" tray_launcher.py --open
  exit /b 0
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw tray_launcher.py --open
  exit /b 0
)
where python >nul 2>nul
if %errorlevel%==0 (
  start "" /min python tray_launcher.py --open
  exit /b 0
)
echo Python was not found. Install Python 3.10 or newer and run:  pip install -r requirements.txt
pause
