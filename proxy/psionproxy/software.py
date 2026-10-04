"""PsionLX's software library, passed through for "Find new software".

PsionLX's package manager (ipkg) and its "Find new software" app fetch
http://<this Mac>:8080/lx/software/<file>. This fetches the same file from the
PsionLX-Software folder of the RetroTechCollection archive, over the HTTPS the
netBook Pro cannot speak, and hands the bytes back untouched -- the proxy's
usual rewriting of pages and images would ruin a package. It serves that one
folder and nothing else: it is not a general file proxy.

/lx/install is the folder's install.sh with this PsionNet's address filled in,
so a stock PsionLX card needs only, as root:
    wget -O - http://<this Mac>:8080/lx/install | sh

config.SOFTWARE_SRC may also be a local folder (run.py --software-src), for
testing a feed before it is uploaded.
"""

import re
import threading
import time
from pathlib import Path

import requests

from . import config

_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*(/[A-Za-z0-9][A-Za-z0-9._+-]*)?$")
MAX_BYTES = 32 * 1024 * 1024          # the largest package is about 1 MB
INDEX_TTL = 60                        # the lists change rarely; packages never do
_INDEXES = ("Packages", "catalogue.txt", "install.sh")
_TYPES = {".ipk": "application/octet-stream", ".png": "image/png",
          ".txt": "text/plain; charset=utf-8", ".sh": "text/plain; charset=utf-8"}

_cache: dict = {}
_lock = threading.Lock()


class NotFound(Exception):
    pass


class Unavailable(Exception):
    pass


def _type(path: str) -> str:
    if path == "Packages":
        return "text/plain; charset=utf-8"
    return _TYPES.get(Path(path).suffix.lower(), "application/octet-stream")


def _read(path: str) -> bytes:
    src = config.SOFTWARE_SRC
    if src.startswith(("http://", "https://")):
        try:
            r = requests.get(f"{src.rstrip('/')}/{path}", timeout=60, stream=True,
                             headers={"User-Agent": "PsionNet (PsionLX software)"})
        except requests.RequestException as exc:
            raise Unavailable(f"the archive could not be reached ({exc.__class__.__name__})")
        if r.status_code == 404:
            raise NotFound(path)
        if r.status_code != 200:
            raise Unavailable(f"the archive answered {r.status_code}")
        body = bytearray()
        for chunk in r.iter_content(65536):
            body += chunk
            if len(body) > MAX_BYTES:
                raise Unavailable("the file is too large")
        return bytes(body)
    root = Path(src).resolve()
    f = (root / path).resolve()
    if root not in f.parents or not f.is_file():
        raise NotFound(path)
    return f.read_bytes()


def fetch(path: str) -> tuple[bytes, str]:
    """A file of the library, exactly as stored, and its content type."""
    if len(path) > 200 or ".." in path or not _SAFE.match(path):
        raise NotFound(path)
    if path in _INDEXES:
        with _lock:
            hit = _cache.get(path)
            if hit and time.time() - hit[0] < INDEX_TTL:
                return hit[1], _type(path)
    body = _read(path)
    if path in _INDEXES:
        with _lock:
            _cache[path] = (time.time(), body)
    return body, _type(path)


_ADDRESS = re.compile(r"^[A-Za-z0-9.-]{1,253}(:[0-9]{1,5})?$")


def reach(host_header: str, bind_host: str, port: int) -> str:
    """How the device reaches this PsionNet: the address it just used (its Host
    header) -- proven reachable from there, through any NAT or emulator --
    or failing that, the address the proxy listens on."""
    h = (host_header or "").strip()
    if _ADDRESS.match(h):
        return h if ":" in h else f"{h}:{port}"
    return f"{bind_host}:{port}"


def install_script(address: str) -> bytes:
    """install.sh, with this PsionNet's address where the archive's copy has @PSIONNET@."""
    body, _ = fetch("install.sh")
    return body.replace(b"@PSIONNET@", address.encode())
