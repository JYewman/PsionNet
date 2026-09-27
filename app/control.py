"""Starting and stopping the two halves of PsionNet.

The PPP link needs root; the proxy does not. Rather than run the whole GUI as
root -- which would put a Tk process, every file it writes and the user's whole
Python environment under the root account -- only the pppd invocation is
elevated, through the standard macOS authorisation dialog.

Nothing persistent is installed: no sudoers entry, no LaunchDaemon, no setuid
helper. The cost is an authentication prompt when starting or stopping the
link. That is the right trade for a tool used a few times a day.
"""

import os
import queue
import sys
import shlex
import signal
import subprocess
import threading
import time
from pathlib import Path

def _root() -> Path:
    """Project root, whether running from source or from a frozen bundle."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


ROOT = _root()


class Runner:
    """Runs a child process, streaming its output into a queue.

    Tk is not thread-safe, so nothing here touches a widget. The GUI drains
    `output` from the main thread with `after()`.
    """

    def __init__(self, name: str):
        self.name = name
        self.output: queue.Queue[str] = queue.Queue()
        self.proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, argv: list[str], cwd: Path | None = None) -> None:
        if self.running:
            return
        self.emit(f"$ {' '.join(argv)}")
        try:
            self.proc = subprocess.Popen(
                argv, cwd=str(cwd or ROOT),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
                start_new_session=True)      # own process group, so we can kill the tree
        except OSError as exc:
            self.emit(f"!! could not start: {exc}")
            return
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            self.output.put(line.rstrip("\n"))
        code = self.proc.wait()
        self.emit(f"-- {self.name} exited ({code})")

    def stop(self) -> None:
        if not self.running:
            return
        assert self.proc
        try:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            try:
                self.proc.terminate()
            except OSError:
                pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            except OSError:
                pass

    def emit(self, line: str) -> None:
        self.output.put(line)

    def drain(self, limit: int = 200) -> list[str]:
        lines = []
        while len(lines) < limit:
            try:
                lines.append(self.output.get_nowait())
            except queue.Empty:
                break
        return lines


# --- privileged operations --------------------------------------------------

def _osascript_admin(shell_cmd: str) -> tuple[bool, str]:
    """Run a shell command as root via the standard macOS auth dialog.

    Returns (ok, output). A user who cancels the dialog produces error -128,
    which is reported as a plain cancellation rather than a failure.
    """
    script = f'do shell script {json_quote(shell_cmd)} with administrator privileges'
    try:
        out = subprocess.run(["/usr/bin/osascript", "-e", script],
                             capture_output=True, text=True, timeout=180)
    except (subprocess.SubprocessError, OSError) as exc:
        return False, str(exc)
    if out.returncode != 0:
        err = (out.stderr or "").strip()
        if "-128" in err:
            return False, "cancelled"
        return False, err or "authorisation failed"
    return True, (out.stdout or "").strip()


def json_quote(s: str) -> str:
    """AppleScript string literal: escape backslashes and double quotes."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def ppp_install() -> tuple[bool, str]:
    """Copy the peer file, pf anchor and ip-up into /etc. One-time."""
    return _osascript_admin(f"/bin/sh {shlex.quote(str(ROOT / 'bin' / 'install.sh'))}")


def ppp_start(device: str) -> tuple[bool, str]:
    """Launch pppd detached on the chosen device.

    The device is appended to `pppd call psion`, which overrides the peer
    file's own device line: pppd registers the device option without
    OPT_PRIO, so setdevname overwrites it unconditionally. That means
    switching ports in the GUI needs no privileged edit of /etc/ppp.

    Deliberately writes NO pid file. An earlier version had root write one
    into /tmp and then ran `kill $(cat ...)`. /tmp is world-writable and
    shell redirection follows symlinks, so any process running as the user
    could aim that write at a file of its choosing -- an arbitrary
    root-write primitive -- or stuff the file with content for `kill` to
    word-split. pppd maintains its own pid file under /var/run, which is
    root-owned, and pkill matches the command line precisely enough.
    """
    # `do shell script` waits for every inherited stdio stream to close, so a
    # backgrounded child must detach all three or the auth dialog hangs for the
    # child's whole lifetime (measured: 4.08 s vs 0.08 s on this machine).
    cmd = (f"/usr/sbin/pppd call psion {shlex.quote(device)} "
           f"</dev/null >/dev/null 2>&1 &")
    return _osascript_admin(cmd)


def ppp_stop() -> tuple[bool, str]:
    """Terminate pppd, matching its command line with an anchored pattern.

    The '^' anchor means this only matches a process whose command line
    STARTS with the pppd invocation, so it cannot catch an editor or shell
    that merely mentions pppd somewhere in its arguments.
    """
    cmd = "/usr/bin/pkill -f '^/usr/sbin/pppd call psion' 2>/dev/null; exit 0"
    return _osascript_admin(cmd)


def pppd_running() -> bool:
    """Is pppd itself running?

    The pattern MUST be anchored. `pgrep -f "pppd call psion"` matches any
    command line containing that text, which includes the osascript helper
    that starts and stops pppd -- so pressing Connect made this return True,
    the button flipped to Disconnect, and every later press ran pkill instead
    of starting anything.
    """
    try:
        out = subprocess.run(
            ["/usr/bin/pgrep", "-f", "^/usr/sbin/pppd call psion"],
            capture_output=True, text=True, timeout=4)
        return out.returncode == 0 and bool(out.stdout.strip())
    except (subprocess.SubprocessError, OSError):
        return False


# --- the proxy (no privilege needed) ---------------------------------------

def proxy_argv(host: str, port: int, fidelity: str, images: bool) -> list[str]:
    """How to launch the proxy.

    Frozen, there is no separate interpreter to call, so the app re-launches
    itself with --run-proxy. From source, use whichever Python is running the
    GUI rather than a hardcoded path.
    """
    if getattr(sys, "frozen", False):
        argv = [sys.executable, "--run-proxy",
                "--host", host, "--port", str(port), "--fidelity", fidelity]
    else:
        argv = [sys.executable or "/usr/local/bin/python3",
                str(ROOT / "app" / "main.py"), "--run-proxy",
                "--host", host, "--port", str(port), "--fidelity", fidelity]
    if not images:
        argv.append("--no-images")
    return argv


def stop_reconnect() -> tuple[bool, str]:
    """Ask Reconnect's daemon to let go of the serial port.

    Quitting the app is the supported way; killing reconnectd alone leaves the
    UI believing it is still connected.
    """
    try:
        subprocess.run(["/usr/bin/osascript", "-e",
                        'tell application "Reconnect" to quit'],
                       capture_output=True, text=True, timeout=15)
    except (subprocess.SubprocessError, OSError) as exc:
        return False, str(exc)
    time.sleep(1.5)
    return True, "asked Reconnect to quit"
