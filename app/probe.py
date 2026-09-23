"""Read-only inspection of everything PsionNet cares about.

No side effects: every function here only observes. The GUI polls these and
renders the result; nothing in this module starts, stops or configures
anything.
"""

import os
import plistlib
import re
import subprocess
from pathlib import Path
import time
from dataclasses import dataclass, field

PPP_LOG = "/var/log/ppp-psion.log"
PROXY_LOG = str(Path.home() / "Library" / "Logs" / "PsionNet" / "proxy-requests.log")
PPP_IFACE = "ppp0"


def _run(cmd, timeout=4):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.stdout
    except (subprocess.SubprocessError, OSError):
        return ""


# --- serial ports -----------------------------------------------------------

@dataclass
class Port:
    device: str                 # /dev/cu.XXXX
    name: str                   # friendly name
    busy_by: str = ""           # process holding it, if any
    likely_psion: bool = False

    @property
    def label(self) -> str:
        bits = [self.name]
        if self.likely_psion:
            bits.append("(Psion adapter)")
        if self.busy_by:
            bits.append(f"- in use by {self.busy_by}")
        return " ".join(bits)


# USB serial bridges people actually use with a Psion cable. The ATEN UC-232A
# on this machine reports ATEN's vendor id (0x0557), not Prolific's, despite
# carrying PL2303 silicon.
_ADAPTER_HINTS = ("uc-232", "usbserial", "ftdi", "pl2303", "usbmodem",
                  "keyserial", "prolific", "ch340", "cp210")
_NOT_SERIAL = ("bluetooth-incoming-port", "debug-console")


def list_ports() -> list[Port]:
    """Every /dev/cu.* that could plausibly carry a Psion cable."""
    ports = []
    try:
        entries = sorted(os.listdir("/dev"))
    except OSError:
        return ports
    for entry in entries:
        if not entry.startswith("cu."):
            continue
        base = entry[3:]
        if base.lower() in _NOT_SERIAL:
            continue
        dev = "/dev/" + entry
        likely = any(h in base.lower() for h in _ADAPTER_HINTS)
        ports.append(Port(device=dev, name=base, likely_psion=likely))
    return ports


def port_holder(device: str) -> str:
    """Which process has this port open, if any. Empty string if free."""
    out = _run(["/usr/sbin/lsof", "-t", "-F", "cn", device], timeout=6)
    name = ""
    for line in out.splitlines():
        if line.startswith("c"):
            name = line[1:]
            break
    return name


def reconnect_holding(device: str) -> bool:
    """Reconnect's daemon takes the port exclusively and blocks pppd.

    Root ignores the TIOCEXCL lock it sets, so pppd would open the port anyway
    and the two would silently corrupt each other's bytes rather than failing
    cleanly. This is a hard precondition, not a warning.
    """
    holder = port_holder(device)
    return holder.lower().startswith("reconnect")


# --- PPP link ---------------------------------------------------------------

@dataclass
class LinkState:
    exists: bool = False
    up: bool = False
    local_ip: str = ""
    remote_ip: str = ""
    stage: str = "down"
    bytes_in: int = 0
    bytes_out: int = 0

    @property
    def summary(self) -> str:
        if self.up:
            return f"Connected  {self.local_ip} -> {self.remote_ip}"
        if self.exists:
            return f"Negotiating ({self.stage})"
        return "Not connected"


_INET = re.compile(r"inet (\d+\.\d+\.\d+\.\d+) --> (\d+\.\d+\.\d+\.\d+)")


def link_state() -> LinkState:
    st = LinkState()
    out = _run(["/sbin/ifconfig", PPP_IFACE])
    if not out:
        st.stage = "down"
        return st
    st.exists = True
    # Parse the real flags word: IFF_UP is 0x1. Substring-matching "UP," also
    # matches MULTICAST and misreads a half-open interface as up.
    fm = re.search(r"flags=([0-9a-fA-F]+)<", out)
    st.up = bool(int(fm.group(1), 16) & 0x1) if fm else False
    m = _INET.search(out)
    if m:
        st.local_ip, st.remote_ip = m.group(1), m.group(2)
        st.up = True
        st.stage = "up"
    else:
        st.stage = _ppp_stage()
    st.bytes_in, st.bytes_out = _link_bytes()
    return st


def _ppp_stage() -> str:
    """Work out how far negotiation got, from pppd's own log."""
    tail = _tail(PPP_LOG, 40)
    if not tail:
        return "waiting"
    for line in reversed(tail):
        if "remote IP address" in line:
            return "up"
        if "IPCP ConfAck" in line or "IPCP ConfReq" in line:
            return "IPCP"
        if "LCP: timeout" in line:
            return "no reply from Psion"
        if "Connection terminated" in line:
            return "disconnected"
        if "LCP ConfReq" in line:
            return "LCP"
    return "waiting"


def _link_bytes() -> tuple[int, int]:
    """Bytes in/out, parsed against netstat's own header.

    Do NOT use fixed column indices: netstat omits the Address field on the
    <Link#> row, so that row has one fewer token than the header and every
    index past Network shifts by one. Reading position 6 there yields Opkts,
    not Ibytes.
    """
    out = _run(["/usr/sbin/netstat", "-I", PPP_IFACE, "-b"])
    lines = out.splitlines()
    if not lines:
        return 0, 0
    header = lines[0].split()
    try:
        i_in, i_out = header.index("Ibytes"), header.index("Obytes")
    except ValueError:
        return 0, 0
    for line in lines[1:]:
        parts = line.split()
        if not parts or parts[0] != PPP_IFACE:
            continue
        # The last seven fields are always Ibytes Opkts Oerrs Obytes Coll (plus
        # Drop on some rows), regardless of whether Address was printed, so
        # counting from the right is correct for both the 10- and 11-field shapes.
        try:
            tail = parts[-7:]
            return int(tail[0]), int(tail[3])
        except (ValueError, IndexError):
            continue
    return 0, 0


class RateMeter:
    """Rolling throughput, from successive byte-counter reads."""

    def __init__(self):
        self._last = None

    def update(self, bytes_in: int, bytes_out: int) -> tuple[float, float]:
        now = time.time()
        if self._last is None:
            self._last = (now, bytes_in, bytes_out)
            return 0.0, 0.0
        t0, i0, o0 = self._last
        dt = now - t0
        if dt < 0.5:
            return 0.0, 0.0
        if bytes_in < i0 or bytes_out < o0:
            # Counters reset -- the interface went away and came back. Rebaseline
            # rather than reporting a huge negative-turned-zero rate.
            self._last = (now, bytes_in, bytes_out)
            return 0.0, 0.0
        self._last = (now, bytes_in, bytes_out)
        return max(0.0, (bytes_in - i0) / dt), max(0.0, (bytes_out - o0) / dt)


# --- routing / NAT ----------------------------------------------------------

def forwarding_enabled() -> bool:
    return _run(["/usr/sbin/sysctl", "-n", "net.inet.ip.forwarding"]).strip() == "1"


def uplink_interface() -> str:
    out = _run(["/sbin/route", "-n", "get", "default"])
    m = re.search(r"interface:\s*(\S+)", out)
    return m.group(1) if m else ""


def ppp_config_installed() -> bool:
    return os.path.exists("/etc/ppp/peers/psion")


# --- proxy ------------------------------------------------------------------

@dataclass
class ProxyState:
    running: bool = False
    address: str = ""
    requests: int = 0
    in_flight: int = 0
    errors: int = 0
    recent: list = field(default_factory=list)


def proxy_state(port: int = 8080) -> ProxyState:
    st = ProxyState()
    out = _run(["/usr/sbin/lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"], timeout=6)
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 9:
            st.running = True
            st.address = parts[8]
            break
    lines = _tail(PROXY_LOG, 400)
    starts = sum(1 for l in lines if " START " in l)
    dones = [l for l in lines if " DONE " in l]
    st.requests = len(dones)
    st.in_flight = max(0, starts - len(dones))
    st.errors = sum(1 for l in dones if re.search(r"-> [45]\d\d ", l))
    st.recent = dones[-12:]
    return st


# --- misc -------------------------------------------------------------------

def _tail(path: str, n: int) -> list[str]:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            block = min(size, n * 200 + 2048)
            fh.seek(size - block)
            data = fh.read().decode("utf-8", "replace")
        return data.splitlines()[-n:]
    except OSError:
        return []


def reconnect_installed() -> bool:
    return os.path.exists("/Applications/Reconnect.app")
