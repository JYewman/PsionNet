"""Stopping the proxy, the app's way, stops librespot too.

    python packaging/ci/check_stop.py

librespot runs in a process group of its own, and before PsionNet 1.3 stopping
the proxy left it running: a "netBook Pro" speaker with nothing behind it. This
starts the proxy through the app's own Runner with Spotify on, a stand-in
librespot and a stand-in login in a throwaway data folder, stops it as the
Stop button does, and checks that nothing is left running.
"""
import os
import site
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="psionnet-stop-"))
WINDOWS = os.name == "nt"
MARK = "psionnet-stand-in-librespot"

# A throwaway data folder, wherever this system keeps PsionNet's.
if WINDOWS:
    os.environ["APPDATA"] = str(TMP / "data")
    data = TMP / "data" / "PsionNet"
elif sys.platform == "darwin":
    os.environ["PYTHONUSERBASE"] = site.getuserbase()     # keep the user's packages
    os.environ["HOME"] = str(TMP / "home")
    data = TMP / "home" / "Library" / "Application Support" / "PsionNet"
else:
    os.environ["XDG_CONFIG_HOME"] = str(TMP / "config")
    data = TMP / "config" / "psionnet"
(data / "librespot").mkdir(parents=True)
(data / "spotify-token.json").write_text(
    '{"access_token": "x", "refresh_token": "y", "client_id": "0123456789abcdef01", '
    '"expires_at": 9999999999}')
(data / "librespot" / "credentials.json").write_text("{}")

# A librespot that only waits, findable by its mark.
bindir = TMP / "bin"
bindir.mkdir()
if WINDOWS:
    (bindir / "librespot.cmd").write_text(
        f'@"{sys.executable}" -c "import time; time.sleep(600)" {MARK} %*\r\n')
else:
    sh = bindir / "librespot"
    sh.write_text(f"#!/bin/sh\n# {MARK}\nwhile :; do sleep 1; done\n")
    sh.chmod(0o755)
os.environ["PATH"] = str(bindir) + os.pathsep + os.environ["PATH"]


def stand_ins() -> list[str]:
    if WINDOWS:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -like '*{MARK}*' }}"
             " | ForEach-Object { $_.ProcessId }"], capture_output=True, text=True).stdout
        return out.split()
    out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout
    return [l.split()[0] for l in out.splitlines() if str(bindir) in l]


sys.path.insert(0, str(ROOT / "app"))
import control  # noqa: E402

r = control.Runner("proxy")
r.start(control.proxy_argv("127.0.0.1", 18766, "medium", True, allow="127.0.0.1/32", spotify=True))
for _ in range(60):
    if stand_ins():
        break
    time.sleep(0.5)
before = stand_ins()
print("stand-in librespot running:", before or "no")
r.stop()
time.sleep(3)
after = stand_ins()
print("after Stop:", after or "nothing left")
print("\n".join(r.drain(400)[-8:]))
ok = bool(before) and not r.running and not after
print("[PASS]" if ok else "[FAIL]", "stopping the proxy stops librespot too")
sys.exit(0 if ok else 1)
