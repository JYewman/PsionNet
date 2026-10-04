"""Run librespot as the netBook Pro's Spotify Connect speaker.

The speaker needs its own sign-in. PsionNet's login (the user's Client ID)
authenticates librespot, but Spotify then refuses that session as a speaker:
"could not initialize spirc: Login request was denied: INVALID_CREDENTIALS"
(librespot 0.8.0, 4 October 2026). Only librespot's own browser sign-in gives
credentials a speaker may use. So, once, librespot signs in on its own; the
credentials it caches are used from then on.

That sign-in runs as a separate, short librespot process, because librespot
prints the sign-in link on stdout ("Browse to: ..."), and in the speaker stdout
is the audio: raw PCM from the pipe backend, read by the Pump. librespot opens
the browser on the link itself. The speaker's log goes to stderr, which is read
here for state and errors.
"""

import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from .. import host as machine
from . import store

DEVICE_NAME = "netBook Pro"
_OAUTH_URL = re.compile(r"https://accounts\.spotify\.com/\S+")
_REFUSED = ("login request was denied", "invalid_credentials", "could not initialize spirc")


EXE = "librespot.exe" if machine.WINDOWS else "librespot"


def binary() -> str | None:
    """The bundled librespot when frozen, else one installed on this computer."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        for cand in (base / "bin" / EXE, Path(sys.executable).resolve().parent / EXE):
            if cand.exists():
                return str(cand)
    found = shutil.which("librespot")
    if found:
        return found
    for cand in ("/opt/homebrew/bin/librespot", "/usr/local/bin/librespot"):
        if os.path.exists(cand):
            return cand
    return None


class Librespot:
    SIGN_IN_TIMEOUT = 600          # seconds to wait for the browser sign-in

    def __init__(self, log=print):
        self.log = log
        self.proc: subprocess.Popen | None = None
        self._signin: subprocess.Popen | None = None
        self.state = "stopped"     # stopped|starting|ready|failed|login
        self.message = ""
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._failures = 0
        self._sign_ins = 0         # one automatic browser sign-in per proxy start, never a loop
        self._refused = False      # Spotify refused the cached credentials as a speaker

    # -- the pump reads this --
    def audio_source(self):
        """The running speaker's stdout, which carries the audio; None if none."""
        p = self.proc
        if p is not None and p.poll() is None and p.stdout is not None:
            return p.stdout
        return None

    def _creds(self) -> Path:
        return store.LIBRESPOT_CACHE / "credentials.json"

    def _common(self, exe: str) -> list[str]:
        store.LIBRESPOT_CACHE.mkdir(parents=True, exist_ok=True)
        os.chmod(store.LIBRESPOT_CACHE, 0o700)
        return [exe,
                "--name", DEVICE_NAME,
                "--device-type", "computer",
                "--cache", str(store.LIBRESPOT_CACHE),
                "--disable-audio-cache",
                # No zeroconf: the speaker is reached through the user's own
                # account, not advertised to anyone on the network.
                "--disable-discovery"]

    def _argv(self, exe: str) -> list[str]:
        """The speaker: cached credentials, audio on stdout."""
        return self._common(exe) + ["--backend", "pipe", "--format", "S16",
                                    "--bitrate", "160", "--initial-volume", "80"]

    def start(self) -> None:
        threading.Thread(target=self._supervise, daemon=True, name="librespot").start()

    def stop(self) -> None:
        self._stop.set()
        self._kill(self._signin)
        self._kill(self.proc)

    @staticmethod
    def _kill(p: subprocess.Popen | None) -> None:
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()

    def _fail(self, message: str) -> None:
        """Give up until the proxy is restarted, rather than retry forever."""
        self.state, self.message = "failed", message
        self.log("librespot: " + message)
        self._stop.wait()

    def _sign_in(self, exe: str) -> bool:
        """librespot's own browser sign-in, in a short process of its own."""
        self._sign_ins += 1
        self.state, self.message = "login", "Opening Spotify's sign-in for the netBook Pro speaker..."
        self.log("librespot: signing in the netBook Pro speaker; librespot opens a browser window")
        argv = self._common(exe) + ["--enable-oauth", "--backend", "pipe", "--device", os.devnull]
        p = self._signin = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            **machine.quiet_child())
        # Read on a thread: Windows cannot wait on a pipe the way POSIX can.
        lines: queue.Queue = queue.Queue()

        def read():
            for raw in iter(p.stdout.readline, b""):
                lines.put(raw.decode("utf-8", "replace").rstrip())
            lines.put(None)
        threading.Thread(target=read, daemon=True, name="librespot-sign-in").start()
        deadline = time.time() + self.SIGN_IN_TIMEOUT
        opened = signed_in = False
        try:
            while time.time() < deadline and not self._stop.is_set():
                if signed_in and self._creds().exists():
                    break
                try:
                    line = lines.get(timeout=1.0)
                except queue.Empty:
                    continue
                if line is None:                    # librespot exited
                    break
                low = line.lower()
                m = _OAUTH_URL.search(line)
                if m and not opened:
                    # librespot opens the browser itself; opening it here too
                    # would give two tabs. If the browser is already signed in
                    # to Spotify, this completes on its own in a few seconds.
                    opened = True
                    self.message = "Sign in to Spotify in the browser window, for the netBook Pro speaker"
                    self.log("librespot: if no browser window opened, visit " + m.group(0))
                elif "authenticated as" in low:
                    signed_in = True
                elif "error" in low or "warn" in low:
                    self.log("librespot sign-in: " + line[-200:])
        finally:
            self._kill(p)
            self._signin = None
        if not opened:
            self.log("librespot: the sign-in never offered a link")
        return signed_in and self._creds().exists()

    def _supervise(self) -> None:
        while not self._stop.is_set():
            exe = binary()
            if not exe:
                self._fail("librespot is not installed" +
                           (" (brew install librespot)" if machine.MAC else ""))
                return
            if not self._creds().exists():
                if self._sign_ins >= 1:
                    self._fail("The netBook Pro speaker is not signed in to Spotify. Stop "
                               "and start the proxy to try the sign-in again.")
                    return
                if not self._sign_in(exe):
                    if self._stop.is_set():
                        return
                    self.state, self.message = "failed", "Spotify sign-in for the speaker did not finish."
                    continue
            self.state, self.message = "starting", ""
            self._refused = False
            argv = self._argv(exe)
            self.log("librespot: " + " ".join(argv[1:]))
            self.proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, bufsize=0,
                                         **machine.quiet_child())
            started = time.time()
            self._read_log(self.proc)
            code = self.proc.wait()
            if self._stop.is_set():
                break
            ran = time.time() - started
            self.log(f"librespot exited ({code}) after {ran:.0f}s")
            if self._refused:
                # These credentials can never run a speaker: drop them and sign
                # in again -- once. Refused again straight after a sign-in means
                # the account itself cannot play (Spotify Connect needs Premium).
                try:
                    self._creds().unlink()
                except OSError:
                    pass
                if self._sign_ins >= 1:
                    self._fail("Spotify refused the netBook Pro speaker even after signing "
                               "in. Spotify Connect speakers need a Premium account.")
                    return
                self.log("librespot: Spotify refused the cached sign-in for a speaker; "
                         "signing in again")
                continue
            self._failures = self._failures + 1 if ran < 20 else 0
            self.state = "failed" if self._failures else "starting"
            time.sleep(min(30, 2 ** min(self._failures, 5)))

    def _read_log(self, proc: subprocess.Popen) -> None:
        def pump():
            for raw in iter(proc.stderr.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip()
                low = line.lower()
                if any(k in low for k in _REFUSED):
                    self._refused = True
                    self.state, self.message = "failed", "Spotify refused the speaker's sign-in."
                elif "authenticated as" in low or "country:" in low:
                    if not self._refused:
                        self.state, self.message = "ready", ""
                        self._failures = 0
                elif "premium" in low and "error" in low:
                    self.state, self.message = "failed", line[-160:]
                # librespot logs a lot; keep what matters
                if any(k in low for k in ("error", "warn", "authenticated", "loading",
                                          "track", "premium", "connect")):
                    self.log("librespot: " + line[-200:])
        threading.Thread(target=pump, daemon=True, name="librespot-log").start()
