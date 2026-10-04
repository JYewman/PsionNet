"""Read-only inspection of everything PsionNet cares about.

No side effects: every function here only observes. The GUI polls these and
renders the result; nothing in this module starts, stops or configures
anything.
"""

import os
import re
import socket
import subprocess
from pathlib import Path
import time
from dataclasses import dataclass, field

import proxypath  # noqa: F401  (makes psionproxy importable)
from psionproxy import host as machine

if machine.LINUX:
    # On Linux pppd runs as the user where it can (groups dip and dialout), and
    # then it may only write a log the user owns.
    PPP_LOG = str(machine.log_dir() / "ppp.log")
else:
    PPP_LOG = "/var/log/ppp-psion.log"
PROXY_LOG = str(machine.log_dir() / "proxy-requests.log")
PPP_IFACE = "ppp0"

# A GUI program on Windows that runs a console tool gets a console window
# flashing up for it, unless told not to.
_QUIET = {"creationflags": subprocess.CREATE_NO_WINDOW} if machine.WINDOWS else {}


def _run(cmd, timeout=4):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **_QUIET)
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
    """Every serial port that could plausibly carry a Psion cable."""
    if machine.LINUX:
        return _linux_ports()
    if not machine.MAC:
        return []                   # Windows: serial links are not supported
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


def _linux_ports() -> list[Port]:
    """USB adapters, and serial ports with a UART behind them.

    The kernel makes ttyS0-31 whether or not the hardware exists; a port with
    nothing behind it reports type 0. /dev/serial/by-id gives USB adapters
    names a person can recognise.
    """
    named = {}
    try:
        for link in sorted(Path("/dev/serial/by-id").iterdir()):
            named[os.path.realpath(link)] = link.name
    except OSError:
        pass
    ports = []
    try:
        entries = sorted(os.listdir("/dev"))
    except OSError:
        return ports
    for entry in entries:
        if entry.startswith("ttyS") and entry[4:].isdigit():
            try:
                with open(f"/sys/class/tty/{entry}/type") as fh:
                    if int(fh.read().strip() or 0) == 0:
                        continue
            except (OSError, ValueError):
                continue
        elif not entry.startswith(("ttyUSB", "ttyACM", "ttyAMA")):
            continue
        dev = "/dev/" + entry
        name = named.get(dev, entry)
        likely = (entry.startswith(("ttyUSB", "ttyACM"))
                  or any(h in name.lower() for h in _ADAPTER_HINTS))
        ports.append(Port(device=dev, name=name, likely_psion=likely))
    return ports


def _linux_holder(device: str) -> str:
    """The process with this port open, found through /proc (no lsof needed).

    Only the user's own processes and pppd started by the user are visible;
    a port held by another user's process looks free, and pppd then reports
    the clash itself.
    """
    try:
        real = os.path.realpath(device)
        pids = [p for p in os.listdir("/proc") if p.isdigit() and int(p) != os.getpid()]
    except OSError:
        return ""
    for pid in pids:
        try:
            fds = os.listdir(f"/proc/{pid}/fd")
        except OSError:
            continue
        for fd in fds:
            try:
                if os.readlink(f"/proc/{pid}/fd/{fd}") == real:
                    with open(f"/proc/{pid}/comm") as fh:
                        return fh.read().strip()
            except OSError:
                continue
    return ""


def port_holder(device: str) -> str:
    """Which process has this port open, if any. Empty string if free."""
    if machine.LINUX:
        return _linux_holder(device)
    if not machine.MAC:
        return ""
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


def _linux_addr(iface: str, request: int) -> str:
    """An interface's own (SIOCGIFADDR) or peer (SIOCGIFDSTADDR) address."""
    import fcntl
    import struct
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        r = fcntl.ioctl(s.fileno(), request, struct.pack("256s", iface.encode()[:15]))
        return socket.inet_ntoa(r[20:24])
    except OSError:
        return ""
    finally:
        s.close()


def _linux_link() -> LinkState:
    st = LinkState()
    base = f"/sys/class/net/{PPP_IFACE}"
    if not os.path.isdir(base):
        return st
    st.exists = True
    try:
        with open(base + "/flags") as fh:
            st.up = bool(int(fh.read().strip(), 16) & 0x1)
    except (OSError, ValueError):
        pass
    local = _linux_addr(PPP_IFACE, 0x8915)
    remote = _linux_addr(PPP_IFACE, 0x8917)
    if local and remote:
        st.local_ip, st.remote_ip = local, remote
        st.up = True
        st.stage = "up"
    else:
        st.stage = _ppp_stage()
    try:
        with open(base + "/statistics/rx_bytes") as fh:
            st.bytes_in = int(fh.read())
        with open(base + "/statistics/tx_bytes") as fh:
            st.bytes_out = int(fh.read())
    except (OSError, ValueError):
        pass
    return st


def link_state() -> LinkState:
    if machine.LINUX:
        return _linux_link()
    st = LinkState()
    if not machine.MAC:
        return st
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
    if machine.LINUX:
        try:
            with open("/proc/sys/net/ipv4/ip_forward") as fh:
                return fh.read().strip() == "1"
        except OSError:
            return False
    return _run(["/usr/sbin/sysctl", "-n", "net.inet.ip.forwarding"]).strip() == "1"


def _source_ip(dest: str = "1.1.1.1") -> str:
    """The local address traffic to dest would leave from. A UDP connect
    sends nothing; it only asks the routing table."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((dest, 53))
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()


def uplink_interface() -> str:
    if machine.LINUX:
        try:
            with open("/proc/net/route") as fh:
                for line in fh.readlines()[1:]:
                    f = line.split()
                    if len(f) > 3 and f[1] == "00000000" and int(f[3], 16) & 0x2:
                        return f[0]
        except (OSError, ValueError):
            pass
        return ""
    if not machine.MAC:
        ip = _source_ip()
        return next((i.name for i in lan_interfaces() if i.ip == ip), "")
    out = _run(["/sbin/route", "-n", "get", "default"])
    m = re.search(r"interface:\s*(\S+)", out)
    return m.group(1) if m else ""


def dns_hijacked_by_vpn(servers=("1.1.1.1", "9.9.9.9")) -> str:
    """Is a VPN capturing the DNS servers we hand to the device?

    A tunnel that claims 1.1.1.1 (Cloudflare WARP does, since it is their own
    resolver) swallows the Psion's DNS queries after NAT: the pf state shows
    NO_TRAFFIC, the device reports "cannot find server", and everything else
    looks perfectly healthy. Worth catching explicitly -- it is invisible
    otherwise and costs hours.

    Returns the offending interface name, or "" if the is clean.
    """
    if not machine.MAC:
        if not machine.LINUX:
            return ""
        by_ip = {i.ip: i.name for i in _ifaddr_interfaces(private_only=False)}
        for server in servers:
            name = by_ip.get(_source_ip(server), "")
            if name.startswith(("tun", "wg", "tailscale", "ppp", "zt", "nordlynx")) and name != PPP_IFACE:
                return name
        return ""
    for server in servers:
        out = _run(["/sbin/route", "-n", "get", server])
        m = re.search(r"interface:\s*(\S+)", out)
        if m and m.group(1).startswith(("utun", "ipsec", "ppp")) and m.group(1) != PPP_IFACE:
            return m.group(1)
    return ""


def ppp_config_installed(kind: str = "epoc") -> bool:
    """Is everything this device type's link needs in /etc?

    Checked per device: an install from before the netBook Pro's modem
    responder was packaged has peers/psion but no /etc/ppp/fakemodem.py, and
    must be offered the install again rather than fail at dial time.
    """
    if machine.LINUX:
        need = ["/etc/ppp/peers/psion", "/etc/ppp/ip-up.d/psionnet"]
        if kind == "ce":
            need += ["/etc/ppp/peers/psion-ce-modem", "/usr/lib/psionnet/fakemodem.py"]
        return all(os.path.exists(p) for p in need)
    need = ["/etc/ppp/peers/psion"]
    if kind == "ce":
        need += ["/etc/ppp/peers/psion-ce-modem", "/etc/ppp/fakemodem.py"]
    return all(os.path.exists(p) for p in need)


def pppd_installed() -> bool:
    return os.path.exists("/usr/sbin/pppd")


@dataclass
class Iface:
    name: str          # en0
    ip: str            # 192.168.1.4
    network: str       # 192.168.1.0/24

    @property
    def label(self) -> str:
        return f"{self.name}  {self.ip}  ({self.network})"


# Interfaces that are never the network a netBook Pro is on: loopback, the
# PPP link, VPN tunnels, and the private networks of containers and VMs.
_NOT_LAN = ("lo", "ppp", "tun", "wg", "tailscale", "zt", "docker", "br-", "veth",
            "virbr", "vboxnet", "vmnet", "lxcbr", "cni", "flannel")


def _ifaddr_interfaces(private_only: bool = True) -> list[Iface]:
    """Every IPv4 address on Windows and Linux, through ifaddr."""
    import ipaddress
    try:
        import ifaddr
        adapters = ifaddr.get_adapters()
    except Exception:
        return []
    found = []
    for ad in adapters:
        name = ad.nice_name if machine.WINDOWS else ad.name
        for ipa in ad.ips:
            if not isinstance(ipa.ip, str):
                continue                        # IPv6
            try:
                ip = ipaddress.ip_address(ipa.ip)
                net = ipaddress.ip_network(f"{ip}/{ipa.network_prefix}", strict=False)
            except ValueError:
                continue
            if private_only and not (ip.is_private and not ip.is_loopback and not ip.is_link_local):
                continue
            if private_only and machine.LINUX and name.startswith(_NOT_LAN):
                continue
            found.append(Iface(name, str(ip), str(net)))
    return found


def lan_interfaces() -> list[Iface]:
    """IPv4 interfaces on a private network: candidates for network mode.

    Loopback, link-local, the PPP link and anything on a public address are
    left out -- the proxy must never be offered to the internet at large.
    """
    if not machine.MAC:
        found = _ifaddr_interfaces()
        main = _source_ip()
        found.sort(key=lambda i: i.ip != main)       # the default route's first
        return found
    import ipaddress
    out = _run(["/sbin/ifconfig"])
    found, name = [], ""
    for line in out.splitlines():
        if line and not line[0].isspace():
            name = line.split(":", 1)[0]
            continue
        bits = line.split()
        if len(bits) >= 4 and bits[0] == "inet" and bits[2] == "netmask":
            try:
                ip = ipaddress.ip_address(bits[1])
                mask = int(bits[3], 16)
                prefix = bin(mask).count("1")
                net = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
            except ValueError:
                continue
            if (ip.is_private and not ip.is_loopback and not ip.is_link_local
                    and not name.startswith(("ppp", "utun", "lo", "bridge"))):
                found.append(Iface(name, str(ip), str(net)))
    return found


# --- proxy ------------------------------------------------------------------

@dataclass
class ProxyState:
    running: bool = False
    address: str = ""
    requests: int = 0
    in_flight: int = 0
    errors: int = 0
    recent: list = field(default_factory=list)


def proxy_state(port: int = 8080, host: str = "") -> ProxyState:
    """Is the proxy listening, and what has it served?

    On a Mac lsof says what is listening on the port. Elsewhere the app asks
    the address it started the proxy on, which needs no tool at all.
    """
    st = ProxyState()
    if machine.MAC:
        out = _run(["/usr/sbin/lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"], timeout=6)
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 9:
                st.running = True
                st.address = parts[8]
                break
    elif host:
        try:
            socket.create_connection((host, port), timeout=0.3).close()
            st.running, st.address = True, f"{host}:{port}"
        except OSError:
            pass
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
    return machine.MAC and os.path.exists("/Applications/Reconnect.app")
