# -*- mode: python ; coding: utf-8 -*-
"""Self-contained PsionNet.app.

Bundles the interpreter, the stdlib, Tk and every dependency, so the app runs
on a Mac with no Python installed at all.
"""
from pathlib import Path

ROOT = Path(SPECPATH)

datas = [
    (str(ROOT / "app" / "icons"), "app/icons"),
    (str(ROOT / "proxy" / "psionproxy" / "assets"), "proxy/psionproxy/assets"),
    (str(ROOT / "etc"), "etc"),
    (str(ROOT / "bin"), "bin"),
    (str(ROOT / "README.md"), "."),
]

a = Analysis(
    [str(ROOT / "app" / "main.py")],
    pathex=[str(ROOT / "app"), str(ROOT / "proxy")],
    datas=datas,
    hiddenimports=[
        "psionproxy", "psionproxy.app", "psionproxy.config", "psionproxy.extract",
        "psionproxy.fetch", "psionproxy.images", "psionproxy.pages",
        "psionproxy.prune", "psionproxy.sanitize", "psionproxy.search",
        "psionproxy.shed", "psionproxy.textmap",
        "gui", "control", "probe", "settings", "resources",
        "flask", "requests", "bs4", "PIL", "certifi",
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
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        "LSApplicationCategoryType": "public.app-category.utilities",
        "NSHumanReadableCopyright":
            "Copyright (C) 2026 Joshua Yeaman. GPL-2.0-or-later. "
            "Contains artwork from Reconnect (GPL-2.0-or-later) and "
            "icons from Psion/Symbian EPOC software.",
    },
)
