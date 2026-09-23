"""Remembered preferences.

The device that last carried a working link is a far better default than any
heuristic about adapter names, so it is stored and preferred above ranking.
"""

import json
from pathlib import Path

DIR = Path.home() / "Library" / "Application Support" / "PsionNet"
FILE = DIR / "settings.json"

_DEFAULTS = {
    "device": "",
    "last_good_device": "",
    "fidelity": "medium",
    "images": True,
    "geometry": "",
}


def load() -> dict:
    data = dict(_DEFAULTS)
    try:
        with open(FILE) as fh:
            stored = json.load(fh)
        if isinstance(stored, dict):
            data.update({k: v for k, v in stored.items() if k in _DEFAULTS})
    except (OSError, ValueError):
        pass
    return data


def save(data: dict) -> None:
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        tmp = FILE.with_suffix(".tmp")
        with open(tmp, "w") as fh:
            json.dump({k: data.get(k, v) for k, v in _DEFAULTS.items()}, fh, indent=2)
        tmp.replace(FILE)
    except OSError:
        pass
