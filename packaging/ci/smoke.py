"""Smoke-test a built PsionNet: the frozen program, run the way the app runs it.

    python packaging/ci/smoke.py dist/PsionNet/PsionNet[.exe]
    python packaging/ci/smoke.py app/main.py          (from source)

The app starts its proxy by running its own program again with --run-proxy.
This does the same, in network mode with the Spotify stand-in and the software
library, and checks what a device would use: the home page, the Spotify text
API and its MP3 stream, the library's catalogue fetched from the archive over
HTTPS, and the installer script. It also checks that the proxy's output reaches
the app's pipe, and that the bundled librespot runs. Exits 1 on any failure.
"""
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

TARGET = Path(sys.argv[1]).resolve()
PORT = 18080
WINDOWS = os.name == "nt"
QUIET = {"creationflags": subprocess.CREATE_NO_WINDOW} if WINDOWS else {}
FAILS = []


def ok(cond, label):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}", flush=True)
    if not cond:
        FAILS.append(label)


def get(path, client=True, limit=4096):
    s = socket.create_connection(("127.0.0.1", PORT), timeout=30)
    hdr = "X-PsionNet-Client: smoke/1\r\n" if client else ""
    s.sendall(f"GET {path} HTTP/1.0\r\nHost: 127.0.0.1:{PORT}\r\n{hdr}\r\n".encode())
    data = b""
    while len(data) < limit:
        chunk = s.recv(4096)
        if not chunk:
            break
        data += chunk
    s.close()
    head, _, body = data.partition(b"\r\n\r\n")
    return head.decode("latin-1"), body


argv = [sys.executable, str(TARGET)] if TARGET.suffix == ".py" else [str(TARGET)]
proc = subprocess.Popen(argv + ["--run-proxy", "--host", "127.0.0.1", "--port", str(PORT),
                                "--allow", "127.0.0.1/32", "--spotify-demo", "--software"],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **QUIET)
lines = []
threading.Thread(target=lambda: [lines.append(l.decode("utf-8", "replace").rstrip())
                                 for l in proc.stdout], daemon=True).start()

up = False
for _ in range(120):
    try:
        socket.create_connection(("127.0.0.1", PORT), timeout=1).close()
        up = True
        break
    except OSError:
        if proc.poll() is not None:
            break
        time.sleep(0.5)
ok(up, "the proxy starts in network mode, with Spotify and the library")
if up:
    head, body = get("/", client=False, limit=200_000)
    ok(" 200 " in head.split("\r\n")[0] and b"PsionNet" in body, "the home page")
    head, body = get("/spotify/playlists")
    ok(" 200 " in head.split("\r\n")[0] and body, "the Spotify text API")
    head, body = get("/spotify/stream.mp3", client=False, limit=24000)
    ok(len(body) > 2 and body[0] == 0xFF and (body[1] & 0xE0) == 0xE0, "the stream is MP3 frames")
    head, body = get("/lx/software/catalogue.txt", client=False, limit=400_000)
    ok(" 200 " in head.split("\r\n")[0] and body.startswith(b"PSIONLX-SOFTWARE 1"),
       "the library's catalogue, from the archive")
    head, body = get("/lx/install", client=False, limit=100_000)
    ok(body.startswith(b"#!/bin/sh") and b"127.0.0.1:18080" in body, "the PsionLX installer")
time.sleep(1)
ok(any(f"PsionNet proxy listening on 127.0.0.1:{PORT}" in l for l in lines),
   "the proxy's output reaches the app's pipe")

if WINDOWS:
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, **QUIET)
else:
    proc.terminate()
proc.wait(timeout=15)

if TARGET.suffix != ".py":
    lib = TARGET.parent / "_internal" / "bin" / ("librespot.exe" if WINDOWS else "librespot")
    if lib.exists():
        r = subprocess.run([str(lib), "--version"], capture_output=True, text=True, timeout=60, **QUIET)
        said = (r.stdout + r.stderr).strip().splitlines()
        ok(r.returncode == 0 and said and "librespot" in said[0].lower(),
           f"the bundled librespot runs: {said[0] if said else r.returncode}")
    else:
        ok(False, f"librespot is bundled ({lib})")

print("\n-- the proxy's last words:\n" + "\n".join(lines[-12:]))
sys.exit(1 if FAILS else 0)
