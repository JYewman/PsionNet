"""Network mode, the PsionLX profile, discovery and the Spotify text API.

Starts the real entry point in network mode with the demo Spotify bridge --
no Spotify account and no librespot needed -- on loopback and a throwaway
port, then speaks to it the way the netBook Pro's app does.
"""
import ipaddress
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FAILS = []


def ok(cond, label):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        FAILS.append(label)


from psionproxy import config, discovery, profiles  # noqa: E402
import psionproxy.app as A  # noqa: E402

# --- which device is asking -------------------------------------------------
FF10_ARM = "Mozilla/5.0 (X11; U; Linux armv5tel; en-GB; rv:1.7.5) Gecko/20041108 Firefox/1.0"
FF128 = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
CE = "Mozilla/4.0 (compatible; MSIE 4.01; Windows CE; netBook Pro)"
EPOC = "EPOC32-WTL/2.0 (VGA)"
ok(profiles.for_user_agent(FF10_ARM).key == "lx", "Firefox 1.0 on PsionLX gets the lx profile")
ok(profiles.for_user_agent(FF128).key != "lx", "a modern Firefox is not mistaken for PsionLX")
ok(profiles.for_user_agent(CE).key == "ce", "Pocket IE still gets the ce profile")
ok(profiles.for_user_agent(EPOC).key == "epoc", "the Series 7 still gets the epoc profile")
ok(profiles.LX.keep_css and not profiles.LX.strict_html32, "lx keeps CSS and HTML 4")

# --- who may use the proxy ----------------------------------------------------
config.apply_network_args("192.168.1.0/24, 10.9.0.0/16", True, False)
ok(config.ALLOWED_NETS == [ipaddress.ip_network("192.168.1.0/24"),
                           ipaddress.ip_network("10.9.0.0/16")], "--allow parses CIDR lists")
ok(config.SPOTIFY and not config.SPOTIFY_DEMO, "--spotify switches the bridge on")
ok(A.client_allowed("192.168.1.77"), "a device on the LAN is served")
ok(A.client_allowed("10.9.3.4"), "a second allowed network is served")
ok(A.client_allowed("127.0.0.1"), "this Mac is always served")
ok(not A.client_allowed("192.168.2.5"), "another subnet is refused")
ok(not A.client_allowed("8.8.8.8"), "the internet is refused")
ok(not A.client_allowed("not an address"), "garbage is refused")
config.apply_network_args("", False, False)
ok(A.client_allowed("8.8.8.8"), "without --allow (the serial link) nothing is filtered")

# --- discovery -------------------------------------------------------------
config.DISCOVERY_PORT = 18899
discovery.start("192.0.2.10", 8080, [ipaddress.ip_network("127.0.0.1/32")])
time.sleep(0.3)
u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
u.settimeout(2)
u.sendto(b"PSIONNET?\n", ("127.0.0.1", 18899))
try:
    reply = u.recvfrom(256)[0]
except OSError:
    reply = b""
ok(reply == b"PSIONNET 192.0.2.10 8080\n", "discovery answers with the proxy's address")
u.sendto(b"HELLO\n", ("127.0.0.1", 18899))
try:
    stray = u.recvfrom(256)[0]
except OSError:
    stray = b""
ok(stray == b"", "discovery ignores anything but PSIONNET?")
u.close()

# --- a stand-in PsionLX-Software folder, for the /lx/ routes --------------------
import os
import tempfile
FEED = Path(tempfile.mkdtemp())
(FEED / "icons").mkdir()
IPK = os.urandom(70_000)                     # bytes no rewriting could leave alone
(FEED / "gpe-tetris_0.6-2-r0_armv5te.ipk").write_bytes(IPK)
(FEED / "Packages").write_text("Package: gpe-tetris\nVersion: 0.6-2-r0\n")
(FEED / "catalogue.txt").write_text("PSIONLX-SOFTWARE 1\n")
(FEED / "install.sh").write_text("#!/bin/sh\nPSIONNET=${1:-@PSIONNET@}\n")
(FEED / "icons" / "gpe-tetris.png").write_bytes(b"\x89PNG\r\n\x1a\n" + os.urandom(64))
(FEED.parent / "secret.txt").write_text("not part of the feed")

# --- the Spotify text API, through the real entry point -----------------------
PORT = 18896
proc = subprocess.Popen(
    [sys.executable, str(ROOT / "run.py"), "--host", "127.0.0.1", "--port", str(PORT),
     "--allow", "127.0.0.1/32", "--spotify-demo", "--software-src", str(FEED)],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def get(path, client=True, limit=4096):
    s = socket.create_connection(("127.0.0.1", PORT), timeout=5)
    hdr = "X-PsionNet-Client: test/1\r\n" if client else ""
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


up = False
for _ in range(40):
    try:
        socket.create_connection(("127.0.0.1", PORT), timeout=1).close()
        up = True
        break
    except OSError:
        time.sleep(0.25)
ok(up, "run.py starts in network mode with the demo bridge")
if up:
    head, body = get("/spotify/hello", client=False)
    ok(head.startswith("HTTP/1.0 200") and body.startswith(b"OK\nstate\tready"),
       "hello answers without the app header")
    head, body = get("/spotify/playlists", client=False)
    ok(body.startswith(b"ERR\t") and " 403 " in head.split("\r\n")[0],
       "control endpoints refuse requests without X-PsionNet-Client")
    head, body = get("/spotify/playlists")
    rows = [r.split(b"\t") for r in body.split(b"\n")[1:] if r]
    ok(body.startswith(b"OK\n") and rows and rows[0][2] == b"Liked Songs",
       "playlists: Liked Songs first, as TAB-separated rows")
    head, body = get("/spotify/tracks?uri=spotify%3Aliked")
    tracks = [r.split(b"\t") for r in body.split(b"\n")[1:] if r]
    ok(tracks and all(t[0] == b"track" and len(t) == 7 for t in tracks),
       "tracks: seven fields per row")
    first = tracks[0][1].decode()
    get(f"/spotify/play?uri={first}&context=spotify%3Aliked")
    head, body = get("/spotify/status")
    st = dict(r.split(b"\t", 1) for r in body.split(b"\n")[1:] if b"\t" in r)
    ok(st.get(b"playing") == b"1" and st.get(b"here") == b"1" and st.get(b"track") == first.encode(),
       "play then status: playing here, the right track")
    get("/spotify/pause")
    head, body = get("/spotify/status")
    ok(b"playing\t0" in body, "pause stops it")
    head, body = get("/spotify/art?u=https%3A%2F%2Fevil.example%2Fx.jpg&s=64", client=False)
    ok(body.startswith(b"ERR\t"), "art refuses anything but Spotify's image hosts")
    head, body = get("/spotify/stream.mp3", client=False, limit=24000)
    ok("audio/mpeg" in head and body[:1] == b"\xff" and (body[1] & 0xE0) == 0xE0,
       "the stream is MP3 frames")
    head, body = get("/spotify/nonsense")
    ok(body.startswith(b"ERR\t"), "unknown requests get ERR, not a stack trace")

    # PsionLX-Software: passed through untouched, that folder only
    head, body = get("/lx/software/gpe-tetris_0.6-2-r0_armv5te.ipk", client=False, limit=200_000)
    ok(" 200 " in head.split("\r\n")[0] and body == IPK and "octet-stream" in head,
       "packages pass through byte for byte")
    head, body = get("/lx/software/icons/gpe-tetris.png", client=False)
    ok("image/png" in head and body.startswith(b"\x89PNG"), "icons pass through, not transcoded")
    head, body = get("/lx/software/Packages", client=False)
    ok(body.startswith(b"Package: gpe-tetris") and "text/plain" in head, "the package list is plain text")
    head, body = get("/lx/install", client=False)
    ok(b"PSIONNET=${1:-127.0.0.1:%d}" % PORT in body, "the installer carries this PsionNet's address")
    s2 = socket.create_connection(("127.0.0.1", PORT), timeout=5)
    s2.sendall(b"GET /lx/install HTTP/1.0\r\nHost: 10.0.2.2:8083\r\n\r\n")
    seen = b""
    while chunk := s2.recv(4096):
        seen += chunk
    s2.close()
    ok(b"PSIONNET=${1:-10.0.2.2:8083}" in seen, "...as the device reached it (its Host header)")
    s2 = socket.create_connection(("127.0.0.1", PORT), timeout=5)
    s2.sendall(b"GET /lx/install HTTP/1.0\r\nHost: x;rm -rf /\r\n\r\n")
    seen = b""
    while chunk := s2.recv(4096):
        seen += chunk
    s2.close()
    ok(b"rm -rf" not in seen and b"PSIONNET=${1:-127.0.0.1:%d}" % PORT in seen,
       "...and never a Host header that is not an address")
    for bad in ("/lx/software/../secret.txt", "/lx/software/icons/../../secret.txt",
                "/lx/software/%2e%2e/secret.txt", "/lx/software/.hidden", "/lx/software/nothing.ipk"):
        head, body = get(bad, client=False)
        ok(" 404 " in head.split("\r\n")[0] and b"not part" not in body, f"refused: {bad}")
    proc.terminate()
else:
    proc.terminate()
    print("  --- output ---")
    print(proc.stdout.read()[:1500])
proc.wait(timeout=10)

# Without --software the routes say so, rather than serving anything.
config.SOFTWARE = False
with A.app.test_request_context("/"):
    r = A._software("/lx/install")
ok(r.status_code == 404 and b"off in PsionNet" in r.get_data(), "/lx/ is off unless asked for")

print("\nALL PASS" if not FAILS else f"\n{len(FAILS)} FAILURES")
sys.exit(1 if FAILS else 0)
