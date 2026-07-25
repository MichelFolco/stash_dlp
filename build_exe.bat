@echo off
REM Builds StashDLP.exe - a single-file, tray-icon Windows executable.
REM Run this from the project root (same folder as tray_launcher.py).

pip install pyinstaller pystray Pillow

pyinstaller --onefile --windowed --name "StashDLP" ^
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
  tray_launcher.py

echo.
echo Build complete: dist\StashDLP.exe
echo Copy icon.ico next to it (same folder) so the tray icon can find it at runtime.
