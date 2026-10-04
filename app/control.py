"""Starting and stopping the two halves of PsionNet.

The PPP link needs root; the proxy does not. Rather than run the whole GUI as
root -- which would put a Tk process, every file it writes and the user's whole
Python environment under the root account -- only the pppd invocation is
elevated: through the standard macOS authorisation dialog, or on Linux through
polkit's (pkexec). On Linux a user in the groups dip and dialout needs neither,
as Debian's pppd is made to be run by them.

Nothing persistent is installed: no sudoers entry, no LaunchDaemon, no setuid
helper. The cost is an authentication prompt when starting or stopping the
link. That is the right trade for a tool used a few times a day.

Windows has no pppd, so there the serial link is not offered at all; devices
on the network need only the proxy.
"""

import os
import queue
import shutil
import sys
import shlex
import signal
import subprocess
import threading
import time
from pathlib import Path

import proxypath  # noqa: F401  (makes psionproxy importable)
from psionproxy import host as machine

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
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        try:
            self.proc = subprocess.Popen(
                argv, cwd=str(cwd or ROOT),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
                **machine.quiet_child())     # own process group, so we can kill the tree
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
        if machine.WINDOWS:
            # No process groups to signal: end the whole tree, librespot with it.
            taskkill = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                    "System32", "taskkill.exe")
            try:
                subprocess.run([taskkill, "/PID", str(self.proc.pid), "/T", "/F"],
                               capture_output=True, timeout=10,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            except (subprocess.SubprocessError, OSError):
                self.proc.kill()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            return
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

def serial_supported() -> bool:
    """A serial link needs pppd: macOS has it, Linux has the ppp package,
    Windows has nothing like it."""
    return machine.MAC or machine.LINUX


def _admin(shell_cmd: str) -> tuple[bool, str]:
    """Run a shell command as root, through the system's password dialog."""
    if machine.LINUX:
        return _pkexec(shell_cmd)
    if not machine.MAC:
        return False, "not available on this system"
    return _osascript_admin(shell_cmd)


def _pkexec(shell_cmd: str) -> tuple[bool, str]:
    """polkit's equivalent of the macOS dialog. pkexec exits 126 when the
    dialog is dismissed and 127 when authorisation fails."""
    pk = shutil.which("pkexec") or "/usr/bin/pkexec"
    if not os.path.exists(pk):
        return False, "pkexec is not installed (sudo apt install pkexec)"
    try:
        out = subprocess.run([pk, "/bin/sh", "-c", shell_cmd],
                             capture_output=True, text=True, timeout=180)
    except (subprocess.SubprocessError, OSError) as exc:
        return False, str(exc)
    if out.returncode == 126:
        return False, "cancelled"
    if out.returncode != 0:
        return False, (out.stderr or "").strip() or "authorisation failed"
    return True, (out.stdout or "").strip()


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
    """Copy the peer file, pf anchor and ip-up into /etc. One-time.

    On Linux the psionnet package installs all of it; running from source,
    packaging/linux/install-ppp.sh does the same job.
    """
    if machine.LINUX:
        script = ROOT / "packaging" / "linux" / "install-ppp.sh"
        if not script.exists():
            return False, "the PPP files are missing: reinstall the psionnet package"
        return _pkexec(f"/bin/sh {shlex.quote(str(script))}")
    return _osascript_admin(f"/bin/sh {shlex.quote(str(ROOT / 'bin' / 'install.sh'))}")


# The peer file to use per device family. They are genuinely different links,
# not a setting: EPOC speaks PPP the moment the port opens, while Windows CE
# drives the line as a Hayes modem at 19200 and must be answered as one.
#
# Windows CE's "Direct Connection" is deliberately absent. It negotiates PPP
# completely and then carries no traffic, because it binds the link to the
# desktop sync stack rather than TCP/IP; see app/README.md.
PROFILES = {
    "epoc": ("psion", "Psion Series 5mx / 7 / netBook / Revo (EPOC)"),
    "ce": ("psion-ce-modem", "Psion netBook Pro (Windows CE, dial-up)"),
}

# Devices that join the LAN themselves (a netBook Pro with a network card)
# need no PPP link at all, only the proxy on the Mac's LAN address.
NETWORK_TYPES = {
    "ce-lan": "Psion netBook Pro (Windows CE, network)",
    "lx-lan": "Psion netBook Pro (PsionLX, network)",
}


def is_network(kind: str) -> bool:
    return kind in NETWORK_TYPES


def ppp_start(device: str, profile: str = "epoc") -> tuple[bool, str]:
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
    peer = PROFILES.get(profile, PROFILES["epoc"])[0]
    if machine.LINUX:
        return _linux_ppp_start(device, peer)
    cmd = (f"/usr/sbin/pppd call {shlex.quote(peer)} {shlex.quote(device)} "
           f"</dev/null >/dev/null 2>&1 &")
    return _osascript_admin(cmd)


PPP_LOG_LINUX = machine.log_dir() / "ppp.log"


def _linux_ppp_start(device: str, peer: str) -> tuple[bool, str]:
    """pppd on Linux: as the user where Debian allows it, else through pkexec.

    Debian installs pppd setuid root, runnable by the group dip; the port
    itself needs the group dialout. A user in both starts the link with no
    password, the peer file supplying the privileged options. Either way pppd
    logs to a file the user owns: run as the user, it may write nowhere else.
    The file is made here first, so that a run as root appends to the user's
    file rather than creating one the user could not write next time.
    """
    if not os.path.exists("/usr/sbin/pppd"):
        return False, "pppd is not installed (sudo apt install ppp)"
    try:
        PPP_LOG_LINUX.parent.mkdir(parents=True, exist_ok=True)
        PPP_LOG_LINUX.touch(exist_ok=True)
    except OSError as exc:
        return False, f"cannot write {PPP_LOG_LINUX}: {exc}"
    argv = ["/usr/sbin/pppd", "call", peer, device, "logfile", str(PPP_LOG_LINUX)]
    if os.access("/usr/sbin/pppd", os.X_OK) and os.access(device, os.R_OK | os.W_OK):
        try:
            subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as exc:
            return False, str(exc)
        return True, "started"
    return _pkexec(" ".join(shlex.quote(a) for a in argv) + " </dev/null >/dev/null 2>&1 &")


def ppp_stop() -> tuple[bool, str]:
    """Terminate pppd, matching its command line with an anchored pattern.

    The '^' anchor means this only matches a process whose command line
    STARTS with the pppd invocation, so it cannot catch an editor or shell
    that merely mentions pppd somewhere in its arguments.
    """
    cmd = "/usr/bin/pkill -f '^/usr/sbin/pppd call psion' 2>/dev/null; exit 0"
    if machine.LINUX:
        # pppd started by the user keeps the user as its real owner, so the
        # user may stop it; one started through pkexec needs pkexec again.
        try:
            subprocess.run(["/usr/bin/pkill", "-f", "^/usr/sbin/pppd call psion"],
                           capture_output=True, timeout=4)
        except (subprocess.SubprocessError, OSError):
            pass
        for _ in range(10):         # pppd says goodbye to the Psion before it exits
            time.sleep(0.5)
            if not pppd_running():
                return True, "stopped"
        return _pkexec(cmd)
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

def proxy_argv(host: str, port: int, fidelity: str, images: bool,
               allow: str = "", spotify: bool = False, software: bool = False) -> list[str]:
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
    if allow:
        argv += ["--allow", allow]
    if spotify:
        argv.append("--spotify")
    if software:
        argv.append("--software")
    return argv


def stop_reconnect() -> tuple[bool, str]:
    """Ask Reconnect's daemon to let go of the serial port.

    Quitting the app is the supported way; killing reconnectd alone leaves the
    UI believing it is still connected.
    """
    if not machine.MAC:
        return False, "Reconnect is a Mac program"
    try:
        subprocess.run(["/usr/bin/osascript", "-e",
                        'tell application "Reconnect" to quit'],
                       capture_output=True, text=True, timeout=15)
    except (subprocess.SubprocessError, OSError) as exc:
        return False, str(exc)
    time.sleep(1.5)
    return True, "asked Reconnect to quit"
