#!/usr/bin/env python3
"""Answer a Windows CE dial-up as if we were a modem, and log everything.

Installed to /etc/ppp/fakemodem.py by bin/install.sh; peers/psion-ce-modem runs it
as its `connect` program.

pppd runs a `connect` program with the serial port as stdin/stdout, so this
reads the device's AT commands from stdin and writes replies to stdout, then
exits 0 to hand the line to pppd.

chat does the same job but logs only to syslog, which macOS is not capturing
here -- so its view of the conversation was invisible. This writes to
/var/log/psionnet-fakemodem.log.
"""
import os, sys, time

# NOT /tmp. This runs as root under pppd, and a predictable name in a
# world-writable directory lets anyone plant a symlink there and have root
# append to a file of their choosing. /var/log is root-owned, and O_NOFOLLOW
# refuses a symlink even so.
LOG = "/var/log/psionnet-fakemodem.log"
TIMEOUT = 90


def log(msg):
    try:
        fd = os.open(LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except OSError:
        pass


def reply(text):
    # Real modems frame responses as CRLF<word>CRLF. CE can be fussy about it.
    out = f"\r\n{text}\r\n".encode()
    os.write(1, out)
    log(f"  --> {text!r}")


log("=" * 50)
log("fakemodem started")
buf = b""
deadline = time.time() + TIMEOUT
try:
    while time.time() < deadline:
        try:
            chunk = os.read(0, 256)
        except OSError:
            time.sleep(0.05); continue
        if not chunk:
            time.sleep(0.05); continue
        buf += chunk
        log(f"<-- {chunk!r}")
        while b"\r" in buf or b"\n" in buf:
            line, _, buf = buf.replace(b"\n", b"\r").partition(b"\r")
            cmd = line.strip().decode("ascii", "replace").upper()
            if not cmd:
                continue
            log(f"  command: {cmd!r}")
            if cmd.startswith("ATD") or cmd.startswith("AT D"):
                reply("CONNECT 19200")
                log("dial seen - handing over to pppd")
                time.sleep(0.3)
                sys.exit(0)
            elif cmd.startswith("AT"):
                reply("OK")
            else:
                log(f"  (ignored: {cmd!r})")
    log("TIMED OUT waiting for a dial command")
    sys.exit(1)
except SystemExit:
    raise
except Exception as exc:
    log(f"ERROR {exc!r}")
    sys.exit(1)
