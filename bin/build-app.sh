#!/bin/sh
# Build PsionNet.app -- a fully self-contained bundle.
#
# PyInstaller bundles the interpreter, the stdlib, Tk and every dependency, so
# the result runs on a Mac with no Python installed at all. Nothing outside the
# bundle is referenced and nothing is written to Application Support at build
# time, so the .app can simply be copied to another machine.
set -e
cd "$(dirname "$0")/.."
PY=${PYTHON:-$(command -v python3)}
[ -n "$PY" ] || { echo "No python3 found. Install Python 3.10+ and retry."; exit 1; }
"$PY" -c 'import tkinter' 2>/dev/null || {
  echo "This Python has no tkinter, which the GUI needs."
  echo "  python.org installers include it; Homebrew needs: brew install python-tk"
  exit 1
}

"$PY" -c 'import PyInstaller' 2>/dev/null || {
  echo "Installing PyInstaller..."
  "$PY" -m pip install --quiet --disable-pip-version-check pyinstaller
}

# Icon: iconutil only understands the standard iconset names and silently
# writes anything else into the .icns as a garbage element.
mkdir -p build_icon
"$PY" - <<'PYEOF'
import pathlib, subprocess, tempfile
from PIL import Image
master = Image.open("app/icons/psionnet_512.png").convert("RGBA")
iso = pathlib.Path(tempfile.mkdtemp()) / "PsionNet.iconset"
iso.mkdir()
for b in (16, 32, 128, 256, 512):
    master.resize((b, b), Image.LANCZOS).save(iso / f"icon_{b}x{b}.png")
    master.resize((b*2, b*2), Image.LANCZOS).save(iso / f"icon_{b}x{b}@2x.png")
subprocess.run(["iconutil", "-c", "icns", str(iso),
                "-o", "build_icon/PsionNet.icns"], check=True)
PYEOF

rm -rf build dist PsionNet.app
"$PY" -m PyInstaller --noconfirm --clean PsionNet.spec
mv dist/PsionNet.app .
rm -rf build dist

# Ad-hoc signature. Copying a bundle breaks any signature it had, and Apple
# Silicon refuses to run unsigned binaries (they die with SIGKILL, exit 137).
codesign -f -s - --deep PsionNet.app 2>/dev/null || codesign -f -s - PsionNet.app

echo
echo "Built PsionNet.app  ($(du -sh PsionNet.app | cut -f1))"
echo
echo "To use it on another Mac: copy it across, then either right-click > Open"
echo "the first time, or run:"
echo "    xattr -dr com.apple.quarantine /path/to/PsionNet.app"
echo "It is ad-hoc signed, not notarised, so Gatekeeper asks once."
