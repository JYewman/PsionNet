"""Where the Spotify bridge keeps its state, and how it is written.

Everything here is private to the user: the OAuth token is a credential, and
librespot's cache holds reusable login credentials. The directory is 0700 and
every file 0600, written atomically so a reader never sees half a token.
"""

import json
import os
import time
from pathlib import Path

from .. import host as machine

DIR = machine.data_dir()
TOKEN = DIR / "spotify-token.json"       # written by the app's login, refreshed by the proxy
STATUS = DIR / "spotify-status.json"     # written by the proxy, read by the app
LIBRESPOT_CACHE = DIR / "librespot"


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def write_json(path: Path, data: dict) -> None:
    _ensure_dir(path.parent)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | machine.NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    # Windows refuses to replace a file another process has open -- the app
    # reading the status, say -- so give it a moment and try again.
    for attempt in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if not machine.WINDOWS or attempt == 9:
                raise
            time.sleep(0.05)


def read_json(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def load_token() -> dict | None:
    tok = read_json(TOKEN)
    if tok and tok.get("access_token") and tok.get("client_id"):
        return tok
    return None


def save_token(tok: dict) -> None:
    write_json(TOKEN, tok)


def forget_login() -> None:
    """Log out: the Web API token and librespot's cached credentials."""
    for path in (TOKEN, LIBRESPOT_CACHE / "credentials.json"):
        try:
            path.unlink()
        except OSError:
            pass


def write_status(**fields) -> None:
    fields["updated"] = time.time()
    try:
        write_json(STATUS, fields)
    except OSError:
        pass


def read_status() -> dict:
    st = read_json(STATUS) or {}
    if time.time() - float(st.get("updated", 0)) > 15:
        st["stale"] = True
    return st
