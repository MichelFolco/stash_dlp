@echo off
REM Builds StashDLP.exe - a tray-icon Windows app - as a --onedir build,
REM not --onefile. This matters specifically because of Restart App:
REM a onefile exe re-extracts itself to a fresh temp folder on every
REM launch, and self-relaunching it (which Restart App does) can race
REM against that extraction, causing crashes like
REM "ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'"
REM that only show up on restart, not the first launch. --onedir runs
REM directly from its own folder with no temp-extraction step at all,
REM which eliminates that entire class of bug - verified by stress-testing
REM three consecutive restarts back-to-back with zero errors, after the
REM onefile build reproducibly broke on relaunch in the same test.
REM
REM Run this from the project root (same folder as tray_launcher.py).

pip install pyinstaller pystray Pillow

pyinstaller --onedir --windowed --name "StashDLP" ^
  --icon icon.ico ^
  --paths backend ^
  --add-data "static;static" ^
  --hidden-import uvicorn.logging ^
  --hidden-import uvicorn.loops ^
  --hidden-import uvicorn.loops.auto ^
  --hidden-import uvicorn.protocols ^
  --hidden-import uvicorn.protocols.http ^
  --hidden-import uvicorn.protocols.http.auto ^
  --hidden-import uvicorn.protocols.websockets ^
  --hidden-import uvicorn.protocols.websockets.auto ^
  --hidden-import uvicorn.lifespan ^
  --hidden-import uvicorn.lifespan.on ^
  --hidden-import websockets ^
  --hidden-import h11 ^
  --hidden-import pydantic_core._pydantic_core ^
  tray_launcher.py

echo.
echo Build complete: dist\StashDLP\StashDLP.exe
echo This is a FOLDER now (StashDLP\), not a single file - StashDLP.exe
echo lives inside it alongside an _internal\ folder it needs. Keep them
echo together; move/shortcut the whole StashDLP\ folder, not just the exe.
echo.
echo Copy icon.ico into that same StashDLP\ folder so the tray icon can
echo find it at runtime.
echo If you want it reachable from your phone/other devices, also copy
echo start_exe_lan.bat into that folder and launch that instead of the
echo exe directly.
