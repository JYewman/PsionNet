#!/bin/sh
# PsionNet, copy config into place. Requires sudo. Reversible via uninstall.sh.
set -e
[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }
SRC=$(cd "$(dirname "$0")/.." && pwd)

# pppd reads /etc/ppp/options on EVERY invocation and aborts if it is absent.
# Leave it empty so this does not alter any other PPP user (VPNs etc).
mkdir -p /etc/ppp/peers /etc/pf.anchors
[ -f /etc/ppp/options ] || { touch /etc/ppp/options; chmod 644 /etc/ppp/options; }

for f in /etc/ppp/peers/psion /etc/ppp/ip-up /etc/ppp/ip-down /etc/pf.anchors/psion.nat; do
  [ -e "$f" ] && [ ! -e "$f.psionnet-backup" ] && cp -p "$f" "$f.psionnet-backup" && echo "backed up $f"
done

install -m 600 -o root -g wheel "$SRC/etc/ppp/peers/psion"      /etc/ppp/peers/psion
install -m 755 -o root -g wheel "$SRC/etc/ppp/ip-up"            /etc/ppp/ip-up
install -m 755 -o root -g wheel "$SRC/etc/ppp/ip-down"          /etc/ppp/ip-down
install -m 644 -o root -g wheel "$SRC/etc/pf.anchors/psion.nat" /etc/pf.anchors/psion.nat

pfctl -vnf /etc/pf.anchors/psion.nat >/dev/null && echo "pf ruleset parses OK"
echo "installed. next: sudo pppd call psion"
