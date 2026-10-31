@echo off
rem Debug launch: server output in this console window, no tray icon. Close the window to stop it.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  set "PATH=%~dp0.venv\Scripts;%PATH%"
  ".venv\Scripts\python.exe" backend\main.py
) else (
  python backend\main.py
)
pause
