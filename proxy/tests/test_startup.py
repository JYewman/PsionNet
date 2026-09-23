"""Smoke test for the startup path.

The wire tests build a server with make_server() directly, which bypasses
main() entirely -- so a NameError in main() passed every test and still broke
on launch. This exercises the real entry point.
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAILS = []


def ok(cond, label):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        FAILS.append(label)


# Every module imports, and every name main() touches resolves.
sys.path.insert(0, str(ROOT))
import psionproxy.app as A  # noqa: E402

for name in ("_ppp_is_up", "main", "proxy", "_budget_images", "_proxy_remote",
             "_serve_image", "_local", "_respond", "_html_response", "_raw_url"):
    ok(hasattr(A, name), f"app.{name} exists")

# Actually launch it the way the user does, on a throwaway port.
proc = subprocess.Popen(
    [sys.executable, str(ROOT / "run.py"), "--host", "127.0.0.1", "--port", "8897"],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
time.sleep(4)
alive = proc.poll() is None
ok(alive, "run.py starts and stays up")
if alive:
    import socket
    s = socket.create_connection(("127.0.0.1", 8897), timeout=5)
    s.sendall(b"GET http://psion/ HTTP/1.0\r\nUser-Agent: EPOC32-WTL/2.0 (VGA)\r\n\r\n")
    head = s.recv(200).decode("latin-1", "replace")
    s.close()
    ok(head.startswith("HTTP/1.0 200"), "serves the home page over HTTP/1.0")
    proc.terminate()
else:
    print("  --- output ---")
    print(proc.stdout.read()[:1500])

proc.wait(timeout=10)
print("\nALL PASS" if not FAILS else f"\n{len(FAILS)} FAILURES")
sys.exit(1 if FAILS else 0)
