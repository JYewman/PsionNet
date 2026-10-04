# -*- mode: python ; coding: utf-8 -*-
"""Self-contained PsionNet, for macOS, Windows and Linux.

Bundles the interpreter, the stdlib, Tk and every dependency, so PsionNet runs
with no Python installed at all. On macOS the result is PsionNet.app. On
Windows and Linux it is the folder dist/PsionNet, from which packaging/ makes
the Windows installer and the Debian package.
"""
import importlib.metadata
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(SPECPATH)
VERSION = "1.3"
BUILD = "4"
MAC = sys.platform == "darwin"
WINDOWS = os.name == "nt"

# librespot (MIT) is the Spotify Connect speaker behind the netBook Pro's
# Spotify app. PSIONNET_LIBRESPOT names a build to bundle (the Windows and Linux
# builds compile their own); otherwise the one installed here (brew install
# librespot). Without it the app still builds, and the Spotify box says what is
# missing.
LIBRESPOT = os.environ.get("PSIONNET_LIBRESPOT") or shutil.which("librespot") or next(
    (p for p in ("/opt/homebrew/bin/librespot", "/usr/local/bin/librespot")
     if Path(p).exists()), None)
binaries = []
licenses = [(str(ROOT / "LICENSE"), "licenses/psionnet"),
            (str(ROOT / "ACKNOWLEDGEMENTS.md"), "licenses/psionnet")]
if LIBRESPOT:
    real = Path(LIBRESPOT).resolve()
    binaries.append((str(real), "bin"))
    named = os.environ.get("PSIONNET_LIBRESPOT_LICENSE")
    if named and Path(named).exists():
        licenses.append((named, "licenses/librespot"))
    elif (real.parent.parent / "LICENSE").exists():          # Homebrew's keg
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
    (str(ROOT / "README.md"), "."),
] + licenses
if MAC:
    # The PPP link's configuration and its installer, for macOS's pppd and pf.
    # The Debian package installs Linux's own; Windows has no PPP.
    datas += [(str(ROOT / "etc"), "etc"), (str(ROOT / "bin"), "bin")]

hidden = [
    "psionproxy", "psionproxy.app", "psionproxy.config", "psionproxy.css",
    "psionproxy.extract", "psionproxy.fetch", "psionproxy.host", "psionproxy.images",
    "psionproxy.pages", "psionproxy.profiles", "psionproxy.prune",
    "psionproxy.sanitize", "psionproxy.search", "psionproxy.shed",
    "psionproxy.textmap", "psionproxy.discovery", "psionproxy.software",
    "psionproxy.spotify", "psionproxy.spotify.audio", "psionproxy.spotify.demo",
    "psionproxy.spotify.librespot", "psionproxy.spotify.routes",
    "psionproxy.spotify.service", "psionproxy.spotify.store",
    "psionproxy.spotify.webapi",
    "gui", "control", "probe", "proxypath", "settings", "resources", "spotify_login",
    "flask", "requests", "bs4", "PIL", "certifi", "lameenc",
    "http.server", "webbrowser",
    "tkinter", "tkinter.ttk", "tkinter.messagebox",
]
if not MAC:
    hidden.append("ifaddr")         # network interfaces, where there is no ifconfig to read

icon = version = None
if WINDOWS:
    # The .exe's icon and the version Explorer shows under Properties.
    from PIL import Image
    (ROOT / "build_icon").mkdir(exist_ok=True)
    icon = str(ROOT / "build_icon" / "PsionNet.ico")
    Image.open(ROOT / "app" / "icons" / "psionnet_512.png").save(
        icon, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    nums = tuple(int(n) for n in VERSION.split(".")) + (0,) * (3 - VERSION.count(".") - 1)
    version = str(ROOT / "build_icon" / "version.txt")
    Path(version).write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums + (int(BUILD),)}, prodvers={nums + (int(BUILD),)},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('080904B0', [
      StringStruct('CompanyName', 'Joshua Yewman'),
      StringStruct('FileDescription', 'PsionNet'),
      StringStruct('FileVersion', '{VERSION}'),
      StringStruct('InternalName', 'PsionNet'),
      StringStruct('LegalCopyright', 'Copyright (C) 2026 Joshua Yewman. GPL-2.0-or-later.'),
      StringStruct('OriginalFilename', 'PsionNet.exe'),
      StringStruct('ProductName', 'PsionNet'),
      StringStruct('ProductVersion', '{VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [2057, 1200])])
  ]
)
""", encoding="utf-8")

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
    hiddenimports=hidden,
    excludes=["pytest", "numpy", "matplotlib", "IPython", "pandas", "scipy"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PsionNet",
          console=False, target_arch=None, codesign_identity=None,
          icon=icon, version=version)

coll = COLLECT(exe, a.binaries, a.datas, name="PsionNet")

if MAC:
    app = BUNDLE(
        coll,
        name="PsionNet.app",
        icon=str(ROOT / "build_icon" / "PsionNet.icns"),
        bundle_identifier="uk.local.psionnet",
        info_plist={
            "CFBundleName": "PsionNet",
            "CFBundleDisplayName": "PsionNet",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": BUILD,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "LSApplicationCategoryType": "public.app-category.utilities",
            "NSHumanReadableCopyright":
                "Copyright (C) 2026 Joshua Yewman. GPL-2.0-or-later. "
                "Contains artwork from Reconnect (GPL-2.0-or-later), "
                "icons from Psion/Symbian EPOC software, librespot (MIT) "
                "and LAME via lameenc (LGPL-3.0-or-later).",
        },
    )
