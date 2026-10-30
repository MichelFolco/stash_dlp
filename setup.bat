@echo off
setlocal
cd /d "%~dp0"
echo ============================================
echo  Genre Manager - setup
echo ============================================
echo.

rem 1. Python 3.10 or newer
set "PY="
where py >nul 2>nul && py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && python -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo Python 3.10 or newer was not found.
  echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH",
  echo then run setup.bat again.
  echo.
  pause
  exit /b 1
)

rem 2. The app's own environment (.venv) and its packages
if not exist ".venv\Scripts\python.exe" (
  echo Creating the environment...
  %PY% -m venv .venv
  if errorlevel 1 goto :failed
)
echo Installing the packages (this takes a minute)...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed
echo.

rem 3. ffmpeg (Downloads tab)
where ffmpeg >nul 2>nul
if errorlevel 1 (
  choice /c YN /m "ffmpeg was not found (needed by the Downloads tab). Install it now with winget"
  if not errorlevel 2 (
    where winget >nul 2>nul
    if errorlevel 1 (
      echo winget is not available. Download ffmpeg from https://www.gyan.dev/ffmpeg/builds/ and add its bin folder to PATH.
    ) else (
      winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
    )
  )
  echo.
)

rem 4. beets (Auto ID after downloads)
choice /c YN /m "Install beets (only needed for Auto ID after downloads)"
if not errorlevel 2 (
  ".venv\Scripts\python.exe" -m pip install beets pylast requests
  if errorlevel 1 goto :failed
)
echo.

echo Done. Next:
echo   1. Install Navidrome and scan your music (see README.md, step 1).
echo   2. Double-click GenreManager.bat. Settings opens on its own the first time.
echo.
pause
exit /b 0

:failed
echo.
echo Setup stopped because a step failed (see the messages above).
echo Fix the problem, or send the messages to whoever shared Genre Manager with you, then run setup.bat again.
pause
exit /b 1
