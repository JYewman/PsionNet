# -*- mode: python ; coding: utf-8 -*-
"""Self-contained PsionNet.app.

Bundles the interpreter, the stdlib, Tk and every dependency, so the app runs
on a Mac with no Python installed at all.
"""
import importlib.metadata
import shutil
from pathlib import Path

ROOT = Path(SPECPATH)

# librespot (MIT) is the Spotify Connect speaker behind the netBook Pro's
# Spotify app. Bundled when this Mac has it (brew install librespot); without
# it the app still builds, and the Spotify box says what is missing.
LIBRESPOT = shutil.which("librespot") or next(
    (p for p in ("/opt/homebrew/bin/librespot", "/usr/local/bin/librespot")
     if Path(p).exists()), None)
binaries = []
licenses = []
if LIBRESPOT:
    real = Path(LIBRESPOT).resolve()
    binaries.append((str(real), "bin"))
    if (real.parent.parent / "LICENSE").exists():          # Homebrew's keg
        licenses.append((str(real.parent.parent / "LICENSE"), "licenses/librespot"))
else:
    print("WARNING: librespot not found -- building without Spotify playback")
# lameenc (LGPL-3.0-or-later, with LAME inside) encodes the MP3 stream.
for f in importlib.metadata.files("lameenc") or []:
    if f.name.upper().startswith(("LICENSE", "COPYING")):
        licenses.append((str(f.locate()), "licenses/lameenc"))

datas = [
    (str(ROOT / "app" / "icons"), "app/icons"),
    (str(ROOT / "proxy" / "psionproxy" / "assets"), "proxy/psionproxy/assets"),
    (str(ROOT / "etc"), "etc"),
    (str(ROOT / "bin"), "bin"),
    (str(ROOT / "README.md"), "."),
] + licenses

a = Analysis(
    [str(ROOT / "app" / "main.py")],
    pathex=[str(ROOT / "app"), str(ROOT / "proxy")],
    binaries=binaries,
    datas=datas,
    # Listed explicitly because several are imported lazily from inside
    # function bodies (`from . import profiles`, `from .css import ...`) to
    # avoid import cycles, and PyInstaller's static analysis does not see
    # those. A module missing here builds cleanly and then fails at runtime
    # in the frozen app only.
    hiddenimports=[
        "psionproxy", "psionproxy.app", "psionproxy.config", "psionproxy.css",
        "psionproxy.extract", "psionproxy.fetch", "psionproxy.images",
        "psionproxy.pages", "psionproxy.profiles", "psionproxy.prune",
        "psionproxy.sanitize", "psionproxy.search", "psionproxy.shed",
        "psionproxy.textmap", "psionproxy.discovery",
        "psionproxy.spotify", "psionproxy.spotify.audio", "psionproxy.spotify.demo",
        "psionproxy.spotify.librespot", "psionproxy.spotify.routes",
        "psionproxy.spotify.service", "psionproxy.spotify.store",
        "psionproxy.spotify.webapi",
        "gui", "control", "probe", "settings", "resources", "spotify_login",
        "flask", "requests", "bs4", "PIL", "certifi", "lameenc",
        "http.server", "webbrowser",
        "tkinter", "tkinter.ttk", "tkinter.messagebox",
    ],
    excludes=["pytest", "numpy", "matplotlib", "IPython", "pandas", "scipy"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PsionNet",
          console=False, target_arch=None, codesign_identity=None)

coll = COLLECT(exe, a.binaries, a.datas, name="PsionNet")

app = BUNDLE(
    coll,
    name="PsionNet.app",
    icon=str(ROOT / "build_icon" / "PsionNet.icns"),
    bundle_identifier="uk.local.psionnet",
    info_plist={
        "CFBundleName": "PsionNet",
        "CFBundleDisplayName": "PsionNet",
        "CFBundleShortVersionString": "1.2",
        "CFBundleVersion": "3",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        "LSApplicationCategoryType": "public.app-category.utilities",
        "NSHumanReadableCopyright":
            "Copyright (C) 2026 Joshua Yeaman. GPL-2.0-or-later. "
            "Contains artwork from Reconnect (GPL-2.0-or-later), "
            "icons from Psion/Symbian EPOC software, librespot (MIT) "
            "and LAME via lameenc (LGPL-3.0-or-later).",
    },
)
