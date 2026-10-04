#!/bin/sh
# Install the PPP link's files on Linux, for PsionNet run from source. The
# Debian package installs the same files; this is only for running without it.
# Run as root (PsionNet asks through pkexec when it finds them missing).
set -e
[ "$(id -u)" = 0 ] || { echo "Run as root."; exit 1; }
HERE=$(cd "$(dirname "$0")" && pwd)
install -d /etc/ppp/peers /etc/ppp/ip-up.d /etc/ppp/ip-down.d /usr/lib/psionnet
install -m 644 "$HERE/peers/psion" "$HERE/peers/psion-ce-modem" /etc/ppp/peers/
install -m 755 "$HERE/ip-up.d/psionnet" /etc/ppp/ip-up.d/psionnet
install -m 755 "$HERE/ip-down.d/psionnet" /etc/ppp/ip-down.d/psionnet
install -m 755 "$HERE/../../etc/ppp/fakemodem.py" /usr/lib/psionnet/fakemodem.py
echo "Installed. To connect without a password, join the groups dip and dialout:"
echo "    sudo usermod -aG dip,dialout \$USER    (then log out and in again)"
