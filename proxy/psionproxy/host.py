"""What differs between the computers PsionNet runs on.

PsionNet began as a Mac app and also runs on Windows and Linux. Everything that
depends on which -- where its files go, what messages call the computer, how a
child process is started out of sight -- is decided here, so the rest of the
code asks rather than assumes. On a Mac every answer is what it always was.
"""

import os
import subprocess
import sys
from pathlib import Path

MAC = sys.platform == "darwin"
WINDOWS = os.name == "nt"
LINUX = sys.platform.startswith("linux")

# What messages call the machine PsionNet runs on.
THIS = "this Mac" if MAC else "this PC" if WINDOWS else "this computer"

# Refuse to open a file through a symlink, where the system can. Windows has no
# such flag, and its user folders are not shared with other users anyway.
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def data_dir() -> Path:
    """Settings, the Spotify token and librespot's cache."""
    if MAC:
        return Path.home() / "Library" / "Application Support" / "PsionNet"
    if WINDOWS:
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "PsionNet"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "psionnet"


def log_dir() -> Path:
    """The proxy's request log and, on Linux, pppd's."""
    if MAC:
        return Path.home() / "Library" / "Logs" / "PsionNet"
    if WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "PsionNet" / "Logs"
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "psionnet"


def quiet_child() -> dict:
    """Popen arguments for a background child: a process group of its own, so
    it can be stopped as a whole, and on Windows no console window."""
    if WINDOWS:
        return {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}
