"""Locate bundled data files, frozen or from source.

PyInstaller puts code in an archive and data files somewhere else entirely, so
`Path(__file__).parent / "icons"` resolves to a path that does not exist once
frozen. On a macOS .app the data lands in Contents/Resources while
`sys._MEIPASS` points at Contents/Frameworks, so both have to be tried.
"""

import sys
from pathlib import Path


def _candidates() -> list[Path]:
    here = Path(__file__).resolve().parent          # .../app
    roots = [here.parent]                           # project root, running from source
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.insert(0, Path(meipass))
        exe = Path(sys.executable).resolve()
        # .../PsionNet.app/Contents/MacOS/PsionNet -> .../Contents/Resources
        roots.insert(0, exe.parent.parent / "Resources")
        roots.insert(1, exe.parent.parent / "Frameworks")
    return roots


def find(*parts: str) -> Path:
    """First existing path matching parts, or the best guess if none exist."""
    rel = Path(*parts)
    for root in _candidates():
        candidate = root / rel
        if candidate.exists():
            return candidate
    return _candidates()[0] / rel


def icons_dir() -> Path:
    return find("app", "icons")
