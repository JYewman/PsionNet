#!/bin/sh
# PsionNet preflight, read-only. Changes nothing.
# Pick the first USB serial adapter, or set DEV=/dev/cu.yourdevice to override.
DEV=${DEV:-$(ls /dev/cu.* 2>/dev/null \
  | grep -viE 'bluetooth|debug-console' \
  | grep -iE 'usbserial|usbmodem|UC-232|FTDI|PL2303|CH34|SLAB|wchusb' \
  | head -1)}
[ -n "$DEV" ] || DEV=$(ls /dev/cu.* 2>/dev/null | grep -viE 'bluetooth|debug-console' | head -1)
[ -n "$DEV" ] || { echo "No serial port found. Plug in the adapter and retry."; exit 1; }
echo "Using serial port: $DEV"
echo "== kernel PPP =="
kmutil showloaded 2>/dev/null | grep -i 'nke.ppp' || echo "  !! com.apple.nke.ppp NOT loaded, serial PPP cannot work"
echo
echo "== pppd =="
[ -x /usr/sbin/pppd ] && echo "  /usr/sbin/pppd present" || echo "  !! missing"
[ -f /etc/ppp/options ] && echo "  /etc/ppp/options present" || echo "  !! /etc/ppp/options missing, pppd will refuse to start (install.sh creates it)"
echo
echo "== serial port =="
if lsof "$DEV" 2>/dev/null | grep -q .; then
  echo "  !! $DEV is HELD by:"; lsof "$DEV" 2>/dev/null | tail -n +2 | sed 's/^/     /'
  echo "     -> Reconnect must release it. Settings > Serial Devices, toggle this device OFF."
  echo "        (root ignores TIOCEXCL, so pppd would open it anyway and the two would"
  echo "         silently steal each other's bytes. This is a hard precondition.)"
else
  echo "  $DEV is free"
fi
stty -f "$DEV" -a >/dev/null 2>&1 && echo "  port opens cleanly" || echo "  port not openable right now"
echo
echo "== uplink =="
IF=$(route -n get default 2>/dev/null | awk '/interface:/{print $2}')
echo "  default route via: ${IF:-none}"
[ "$IF" != "en0" ] && echo "     -> uplink is not en0; edit 'uplink' in etc/pf.anchors/psion.nat"
route -n get default 2>/dev/null | awk '/interface:/{print $2}' | grep -q utun && \
  echo "     !! default route is a VPN tunnel, the NAT rule will not match"
echo "  ip forwarding: $(sysctl -n net.inet.ip.forwarding)"
for D in 1.1.1.1 9.9.9.9; do
  VIA=$(route -n get "$D" 2>/dev/null | awk "/interface:/{print \$2}")
  case "$VIA" in
    utun*|ipsec*)
      echo "  !! DNS $D routes via $VIA (a VPN tunnel)."
      echo "     The device's DNS queries will be swallowed after NAT and it"
      echo "     will report 'cannot find server' while everything else looks"
      echo "     healthy. Disconnect the VPN, or change ms-dns in the peer file"
      echo "     to a resolver the VPN does not claim."
      ;;
  esac
done
echo
echo "== installed? =="
for f in /etc/ppp/peers/psion /etc/pf.anchors/psion.nat /etc/ppp/ip-up; do
  [ -e "$f" ] && echo "  $f installed" || echo "  $f not installed"
done
