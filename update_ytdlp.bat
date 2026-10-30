@echo off
rem yt-dlp breaks when YouTube changes; updating it fixes most "download failed" errors. Restart Genre Manager afterwards.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install --upgrade yt-dlp
pause
