"""Remembered preferences.

The device that last carried a working link is a far better default than any
heuristic about adapter names, so it is stored and preferred above ranking.
"""

import json

import proxypath  # noqa: F401  (makes psionproxy importable)
from psionproxy import host as machine

DIR = machine.data_dir()
FILE = DIR / "settings.json"

_DEFAULTS = {
    "device": "",
    "last_good_device": "",
    "device_type": "epoc",
    "fidelity": "medium",
    "images": True,
    "geometry": "",
    "lan_iface": "",             # network mode: the interface the proxy listens on
    "spotify_client_id": "",     # the user's own Spotify developer app
    "spotify_open": False,       # the Spotify section unfolded in the window
}


def load() -> dict:
    data = dict(_DEFAULTS)
    try:
        with open(FILE, encoding="utf-8") as fh:
            stored = json.load(fh)
        if isinstance(stored, dict):
            data.update({k: v for k, v in stored.items() if k in _DEFAULTS})
        # The netBook Pro's Windows CE "Direct Connection" option was removed:
        # it negotiates PPP and then carries no traffic. Dial-up is the route.
        if data.get("device_type") == "ce-direct":
            data["device_type"] = "ce"
    except (OSError, ValueError):
        pass
    return data


def save(data: dict) -> None:
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        tmp = FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({k: data.get(k, v) for k, v in _DEFAULTS.items()}, fh, indent=2)
        tmp.replace(FILE)
    except OSError:
        pass
