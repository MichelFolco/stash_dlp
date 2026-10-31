"""
tray_launcher.py - StashDLP tray launcher.

Runs the server in the background and puts the app icon in the system tray.

    python tray_launcher.py            start (tray icon only)
    python tray_launcher.py --open     start and open the app in the browser once the server is ready

One-time setup:   pip install -r requirements.txt     (adds pystray and pillow)
Normal launch:    start_tray.bat                       (no console window; uses pythonw)
LAN launch:       start_tray_lan.bat                   (server listens on 0.0.0.0)
Live console:     start_console.bat                    (debug: server output in a window, no tray)

Tray icon
    Plain logo ............ server running
    + amber dot ........... starting
    + grey dot ............ stopped
    + red dot ............. stopped unexpectedly (see "View server log")
    Click / double-click opens the app. Right-click for: Restart server, Stop/Start server, Copy phone
    address (only when the server listens on the LAN), View server log, Start with Windows, Quit.

Files written next to this script:   logs/server.log (+ .1), logs/launcher.log, .launcher.lock
Settings by environment variable:    STASH_DLP_PORT (default 8722), STASH_DLP_HOST (default 127.0.0.1)

The server is started as "uvicorn main:app" from backend/. The in-app "Restart" action cannot relaunch
itself under the tray, so it exits with RESTART_EXIT_CODE and this launcher starts a fresh server.
"""

from __future__ import annotations

import argparse
import atexit
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR / "backend"
STATIC_DIR = APP_DIR / "static"
LOG_DIR = APP_DIR / "logs"
SERVER_LOG = LOG_DIR / "server.log"
LOCK_FILE = APP_DIR / ".launcher.lock"

APP_NAME = "StashDLP"
IS_WIN = os.name == "nt"
HOST = os.environ.get("STASH_DLP_HOST", "127.0.0.1")
PORT = int(os.environ.get("STASH_DLP_PORT", "8722"))
LOCAL_URL = f"http://127.0.0.1:{PORT}"
READY_URL = f"http://127.0.0.1:{PORT}/api/app_version"
LAN_BOUND = HOST not in ("127.0.0.1", "localhost", "::1")

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
MAX_LOG_BYTES = 2 * 1024 * 1024
READY_TIMEOUT_S = 180
RESTART_EXIT_CODE = 75          # must match backend/main.py (_do_restart)

log = logging.getLogger("launcher")


def app_version() -> str:
    """APP_VERSION from backend/config.py, read as text so the launcher never imports the app."""
    try:
        m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', (BACKEND_DIR / "config.py").read_text(encoding="utf-8"), re.M)
        return m.group(1) if m else ""
    except OSError:
        return ""


# ---- small helpers ---------------------------------------------------------------------------

def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.FileHandler(LOG_DIR / "launcher.log", encoding="utf-8")]
    if sys.stderr is not None:          # None under pythonw
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)


def acquire_single_instance():
    """Returns an open, locked file for as long as this process lives, or None if another launcher holds it."""
    fh = open(LOCK_FILE, "a+")
    try:
        if IS_WIN:
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def port_in_use(port: int = PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_port_free(port: int = PORT, timeout: float = 10.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if not port_in_use(port):
            return True
        time.sleep(0.25)
    return not port_in_use(port)


def server_answers() -> bool:
    try:
        with urllib.request.urlopen(READY_URL, timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))     # no packet is sent; this just picks the outgoing interface
            return s.getsockname()[0]
    except OSError:
        return ""


def kill_tree(pid: int) -> None:
    """Hard stop of a process and everything it started (yt-dlp and ffmpeg children included)."""
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=CREATE_NO_WINDOW)
        else:
            os.killpg(pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass


def kill_port_listener(port: int = PORT) -> None:
    """Last resort on Windows: something still holds our port after a stop (an orphaned worker). Only
    processes LISTENING on exactly this port are touched."""
    if not IS_WIN:
        return
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW).stdout
    except OSError:
        return
    pids = {line.split()[-1] for line in out.splitlines() if "LISTENING" in line and line.split()[1].endswith(f":{port}")}
    for pid in pids:
        if pid.isdigit() and int(pid) != os.getpid():
            log.warning("Port %s still held by PID %s after stop; ending it", port, pid)
            kill_tree(int(pid))


def server_python() -> str:
    """The server runs under python.exe (hidden by CREATE_NO_WINDOW), never pythonw.exe: under pythonw,
    sys.stdout can be None and uvicorn's log formatter then fails."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").exists():
        return str(exe.with_name("python.exe"))
    return str(exe)


# ---- the server process ----------------------------------------------------------------------

class Server:
    """Owns the uvicorn child. States: stopped, starting, running, crashed. Every change calls on_change()."""

    def __init__(self, on_change):
        self.on_change = on_change
        self.state = "stopped"
        self.proc: subprocess.Popen | None = None
        self._lock = threading.RLock()
        self._gen = 0                   # bumped by every start/stop so a stale watcher never reports for a newer process
        self._busy = threading.Event()  # set while a restart is in flight
        self.last_error = ""

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def restarting(self) -> bool:
        return self._busy.is_set()

    def _set(self, state: str) -> None:
        self.state = state
        try:
            self.on_change(state)
        except Exception:
            log.exception("on_change failed")

    def _rotate_log(self) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            if SERVER_LOG.exists() and SERVER_LOG.stat().st_size > MAX_LOG_BYTES:
                os.replace(SERVER_LOG, SERVER_LOG.with_suffix(".log.1"))
        except OSError:
            pass

    def start(self) -> bool:
        with self._lock:
            if self.alive:
                return True
            if port_in_use() and not wait_port_free(timeout=8):
                self.last_error = f"Port {PORT} is already in use by another program."
                log.error(self.last_error)
                self._set("crashed")
                return False
            self._rotate_log()
            logfile = open(SERVER_LOG, "ab")
            logfile.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} starting server (launcher pid {os.getpid()}) =====\n".encode())
            logfile.flush()
            cmd = [server_python(), "-m", "uvicorn", "main:app", "--app-dir", str(BACKEND_DIR), "--host", HOST, "--port", str(PORT)]
            env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", STASH_DLP_TRAY="1")
            kwargs: dict = dict(cwd=APP_DIR, stdout=logfile, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env)
            if IS_WIN:
                kwargs["creationflags"] = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
            try:
                self.proc = subprocess.Popen(cmd, **kwargs)
            except OSError as e:
                self.last_error = str(e)
                log.exception("Could not start the server")
                self._set("crashed")
                return False
            finally:
                logfile.close()
            self._gen += 1
            self.last_error = ""
            self._set("starting")
            threading.Thread(target=self._watch, args=(self._gen, self.proc), daemon=True).start()
            return True

    def _watch(self, gen: int, proc: subprocess.Popen) -> None:
        """Flip to running when the server answers; report if the process dies on its own."""
        deadline = time.time() + READY_TIMEOUT_S
        while time.time() < deadline and proc.poll() is None and gen == self._gen:
            if server_answers():
                if gen == self._gen:
                    self._set("running")
                break
            time.sleep(0.5)
        proc.wait()
        if gen != self._gen:           # this exit was asked for (stop/restart)
            return
        if proc.returncode == RESTART_EXIT_CODE:       # the app's own "Restart" menu entry
            log.info("Server asked for a restart; starting a new one")
            self.start()
            return
        self.last_error = f"The server stopped unexpectedly (exit code {proc.returncode}). See the server log."
        log.error(self.last_error)
        self._set("crashed")

    def stop(self) -> None:
        with self._lock:
            self._gen += 1             # silences the watcher: this exit is intentional
            proc, self.proc = self.proc, None
            if proc is not None and proc.poll() is None:
                # The server has no console to receive Ctrl+Break from a windowless launcher, so on Windows it is
                # ended as a tree (this also takes its yt-dlp/ffmpeg children with it).
                if IS_WIN:
                    kill_tree(proc.pid)
                else:
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except (OSError, ProcessLookupError):
                        pass
                try:
                    proc.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    log.warning("Server ignored the stop request; killing it")
                    kill_tree(proc.pid)
                    proc.wait()
            if not wait_port_free(timeout=4):
                kill_port_listener()
                wait_port_free(timeout=4)
            self._set("stopped")

    def restart(self) -> bool:
        if self._busy.is_set():
            return False
        self._busy.set()
        try:
            self.stop()
            return self.start()
        finally:
            self._busy.clear()
            self.on_change(self.state)


# ---- icon ------------------------------------------------------------------------------------

DOT_COLORS = {"starting": (232, 163, 61, 255), "stopped": (140, 140, 140, 255), "crashed": (220, 60, 50, 255)}
ICON_SOURCES = (STATIC_DIR / "icons" / "icon-64.png", STATIC_DIR / "logo.png", STATIC_DIR / "favicon.png")


def base_icon():
    """The first image found in ICON_SOURCES, cropped to its artwork and centred on a 64 px square.
    With none present, a plain placeholder - the tray must never fail to start over a missing logo."""
    from PIL import Image, ImageDraw
    for src in ICON_SOURCES:
        try:
            logo = Image.open(src).convert("RGBA")
        except (OSError, ValueError):
            continue
        box = logo.getchannel("A").getbbox()
        if box:
            logo = logo.crop(box)
        side = max(logo.size)
        img = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        img.paste(logo, ((side - logo.width) // 2, (side - logo.height) // 2), logo)
        return img.resize((64, 64), Image.LANCZOS)
    log.warning("No icon image found in static/; using a placeholder")
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4, 4, 60, 60), radius=12, fill=(58, 122, 196, 255))
    d.ellipse((20, 20, 44, 44), fill=(255, 255, 255, 255))
    return img


def make_icon(state: str):
    """The app icon; while the server is not running a small status dot is added bottom-right."""
    from PIL import ImageDraw
    img = base_icon()
    color = DOT_COLORS.get(state)
    if color:
        d = ImageDraw.Draw(img)
        d.ellipse((38, 38, 62, 62), fill=(23, 21, 18, 255))          # dark ring so the dot reads on any taskbar
        d.ellipse((42, 42, 58, 58), fill=color)
    return img


STATE_LABEL = {
    "running": "running",
    "starting": "starting…",
    "stopped": "stopped",
    "crashed": "stopped unexpectedly",
}


# ---- Windows autostart -----------------------------------------------------------------------

def startup_script() -> Path | None:
    if not IS_WIN or not os.environ.get("APPDATA"):
        return None
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "StashDLP.vbs"


def autostart_enabled() -> bool:
    p = startup_script()
    return bool(p and p.exists())


def set_autostart(enabled: bool) -> None:
    """A tiny .vbs in the Startup folder: it launches the tray hidden, with no console flash and no browser tab.
    A host or port set in the environment when this is switched on is carried into the script."""
    p = startup_script()
    if not p:
        return
    if not enabled:
        p.unlink(missing_ok=True)
        return
    pyw = Path(sys.executable).with_name("pythonw.exe")
    exe = pyw if pyw.exists() else Path(sys.executable)
    script = APP_DIR / "tray_launcher.py"
    env_lines = "".join(
        f'sh.Environment("Process")("{name}") = "{os.environ[name]}"\r\n'
        for name in ("STASH_DLP_HOST", "STASH_DLP_PORT") if name in os.environ
    )
    p.write_text(
        'Set sh = CreateObject("WScript.Shell")\r\n'
        f'sh.CurrentDirectory = "{APP_DIR}"\r\n'
        f"{env_lines}"
        f'sh.Run """{exe}"" ""{script}""", 0, False\r\n',
        encoding="utf-8",
    )


# ---- the tray application --------------------------------------------------------------------

class TrayApp:
    def __init__(self, open_browser_when_ready: bool):
        import pystray
        self.pystray = pystray
        self.open_when_ready = open_browser_when_ready
        self.server = Server(self.on_state)
        self.icon = pystray.Icon("stash_dlp", make_icon("starting"), self.tooltip("starting"), self.build_menu())

    # -- state -> icon / tooltip / notifications
    def tooltip(self, state: str) -> str:
        v = app_version()
        return f"{APP_NAME}{' ' + v if v else ''} - {STATE_LABEL.get(state, state)} (port {PORT})"[:127]

    def on_state(self, state: str) -> None:
        self.icon.icon = make_icon(state)
        self.icon.title = self.tooltip(state)
        try:
            self.icon.update_menu()
        except Exception:
            pass
        if state == "running" and self.open_when_ready:
            self.open_when_ready = False          # first start only; a restart must not pop a new tab
            self.open_app()
        elif state == "crashed":
            self.notify(self.server.last_error or "The server stopped unexpectedly.")

    def notify(self, message: str, title: str = APP_NAME) -> None:
        try:
            if self.icon.HAS_NOTIFICATION:
                self.icon.notify(message, title)
        except Exception:
            log.debug("notify failed", exc_info=True)

    # -- menu
    def build_menu(self):
        P = self.pystray
        s = self.server
        items = [
            P.MenuItem(lambda item: f"Server: {STATE_LABEL.get(s.state, s.state)}", None, enabled=False),
            P.Menu.SEPARATOR,
            P.MenuItem(f"Open {APP_NAME}", lambda icon, item: self.open_app(), default=True),
            P.MenuItem(lambda item: f"Copy phone address ({lan_ip()}:{PORT})" if IS_WIN else f"Open phone address ({lan_ip()}:{PORT})",
                       lambda icon, item: self.lan_address(), visible=lambda item: LAN_BOUND and bool(lan_ip())),
            P.Menu.SEPARATOR,
            P.MenuItem("Restart server", lambda icon, item: self.bg(self.do_restart), enabled=lambda item: not s.restarting),
            P.MenuItem(lambda item: "Stop server" if s.state in ("running", "starting") else "Start server",
                       lambda icon, item: self.bg(self.do_toggle), enabled=lambda item: not s.restarting),
            P.Menu.SEPARATOR,
            P.MenuItem("View server log", lambda icon, item: self.open_log()),
        ]
        if IS_WIN:
            items.append(P.MenuItem("Start with Windows", lambda icon, item: self.toggle_autostart(), checked=lambda item: autostart_enabled()))
        items += [P.Menu.SEPARATOR, P.MenuItem("Quit", lambda icon, item: self.bg(self.do_quit))]
        return P.Menu(*items)

    # -- actions
    @staticmethod
    def bg(fn) -> None:
        """Menu callbacks run on the tray's own thread; anything slow goes to a worker so the menu never freezes."""
        threading.Thread(target=fn, daemon=True).start()

    def open_app(self) -> None:
        webbrowser.open(LOCAL_URL)

    def lan_address(self) -> None:
        url = f"http://{lan_ip()}:{PORT}"
        if IS_WIN:
            subprocess.run(["clip"], input=url.encode("ascii"), creationflags=CREATE_NO_WINDOW)
            self.notify(f"Copied {url}")
        else:
            webbrowser.open(url)

    def do_restart(self) -> None:
        self.on_state("starting")
        if self.server.restart():
            self.notify("Server restarted.")

    def do_toggle(self) -> None:
        if self.server.state in ("running", "starting"):
            self.server.stop()
        else:
            self.server.start()

    def open_log(self) -> None:
        SERVER_LOG.parent.mkdir(parents=True, exist_ok=True)
        SERVER_LOG.touch()
        if IS_WIN:
            os.startfile(SERVER_LOG)          # noqa: S606 - opens in the user's default text editor
        else:
            subprocess.Popen(["xdg-open", str(SERVER_LOG)])

    def toggle_autostart(self) -> None:
        set_autostart(not autostart_enabled())
        self.icon.update_menu()

    def do_quit(self) -> None:
        self.server.stop()
        self.icon.stop()

    # -- lifecycle
    def run(self) -> None:
        def setup(icon):
            icon.visible = True
            self.bg(self.server.start)
        atexit.register(self.server.stop)
        self.icon.run(setup=setup)


def hide_console() -> None:
    """Started with plain python.exe (double-clicked .py)? Hide the console; pythonw never has one."""
    if not IS_WIN:
        return
    import ctypes
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 0)


def fatal(message: str) -> None:
    log.error(message)
    if IS_WIN:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, APP_NAME, 0x10)
    else:
        print(message, file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="StashDLP tray launcher")
    ap.add_argument("--open", action="store_true", help="open the app in the browser once the server is ready")
    ap.add_argument("--keep-console", action="store_true", help="do not hide the console window")
    args = ap.parse_args(argv)

    setup_logging()
    lock = acquire_single_instance()
    if lock is None:
        log.info("Launcher already running; opening the app instead")
        webbrowser.open(LOCAL_URL)
        return 0

    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError as e:
        fatal(f"Missing package: {e.name}.\n\nRun:  pip install -r requirements.txt")
        return 1

    if not args.keep_console:
        hide_console()
    TrayApp(args.open).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
