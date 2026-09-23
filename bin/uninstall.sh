#!/bin/sh
# PsionNet — undo everything install.sh did.
[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }
pfctl -a com.apple/psion.nat -F all 2>/dev/null && echo "flushed pf anchor"
for f in /etc/ppp/peers/psion /etc/ppp/ip-up /etc/ppp/ip-down /etc/pf.anchors/psion.nat; do
  rm -f "$f"
  b="$f.psionnet-backup"
  [ -e "$b" ] || continue
  # If the "backup" is our own hook (install.sh ran twice), restoring it would
  # leave a PsionNet root hook in place while claiming to have removed it.
  if grep -q 'psion-ppp\|PsionNet' "$b" 2>/dev/null; then
    rm -f "$b"; echo "discarded stale PsionNet backup of $f"
  else
    mv "$b" "$f"; echo "restored $f"
  fi
done
sysctl -w net.inet.ip.forwarding=0 >/dev/null
echo "removed. /etc/ppp/options left in place (harmless, empty)."
echo "pf itself left enabled — 'sudo pfctl -d' if you had it off before."
