"""Let a device on the LAN find PsionNet without typing an address.

The PsionLX Spotify app broadcasts "PSIONNET?" to UDP port 8899 and this
answers "PSIONNET <address> <port>" -- the address the proxy is bound to. It
answers only devices the proxy itself would serve (config.ALLOWED_NETS), and
it reveals nothing the proxy's own listening socket does not already.
"""

import ipaddress
import socket
import threading

from . import config

_started = False


def _serve(host: str, port: int, nets: list) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        # Broadcasts are only delivered to a socket bound to the wildcard
        # address; replies still name the one address the proxy listens on.
        sock.bind(("", config.DISCOVERY_PORT))
    except OSError as exc:
        print(f"note: discovery unavailable ({exc}); enter the address on the device")
        return
    reply = f"PSIONNET {host} {port}\n".encode()
    while True:
        try:
            data, (src, sport) = sock.recvfrom(512)
        except OSError:
            continue
        if not data.startswith(b"PSIONNET?"):
            continue
        try:
            addr = ipaddress.ip_address(src)
        except ValueError:
            continue
        if not any(addr in net for net in nets):
            continue
        try:
            sock.sendto(reply, (src, sport))
        except OSError:
            pass


def start(host: str, port: int, nets: list) -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_serve, args=(host, port, nets), daemon=True,
                     name="discovery").start()
