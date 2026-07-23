"""Native OS folder-picker, using tkinter's filedialog. This pops up on
whatever machine is running the backend - fine for the normal case
(you're browsing to your own PC), but if you're reaching the app
remotely (e.g. over Tailscale from your phone), the dialog appears on
the PC, not your phone. main.py gates this endpoint to localhost-only
requests to avoid a confusing "nothing happened on my phone" moment.
"""
import asyncio


def _ask_directory_sync(initial_dir: str) -> str:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        path = filedialog.askdirectory(
            initialdir=initial_dir or None,
            title="Select Download Folder",
        )
    finally:
        root.destroy()
    return path or ""


async def ask_directory(initial_dir: str = "") -> str:
    return await asyncio.to_thread(_ask_directory_sync, initial_dir)
