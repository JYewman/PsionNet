#!/bin/sh
# PsionNet, undo everything install.sh did -- and what earlier versions did.
[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }
pfctl -a com.apple/psion.nat -F all 2>/dev/null && echo "flushed pf anchor"
for f in /etc/ppp/peers/psion /etc/ppp/peers/psion-ce-modem /etc/ppp/fakemodem.py \
         /etc/ppp/peers/psion-ce /etc/ppp/peers/psion-ce-mschap /etc/ppp/psion-ce.chat \
         /etc/ppp/pap-secrets /etc/ppp/chap-secrets \
         /etc/ppp/ip-up /etc/ppp/ip-down /etc/pf.anchors/psion.nat; do
  case "$f" in
    */pap-secrets|*/chap-secrets)
      # Not ours unless it says so: other PPP users keep their secrets.
      [ -e "$f" ] && ! grep -q 'PsionNet' "$f" 2>/dev/null && continue ;;
  esac
  rm -f "$f"
  b="$f.psionnet-backup"
  [ -e "$b" ] || continue
  # If the "backup" is our own hook (install.sh ran twice), restoring it would
  # leave a PsionNet root hook in place while claiming to have removed it.
  if grep -q 'psion-ppp\|PsionNet\|psionnet' "$b" 2>/dev/null; then
    rm -f "$b"; echo "discarded stale PsionNet backup of $f"
  else
    mv "$b" "$f"; echo "restored $f"
  fi
done
sysctl -w net.inet.ip.forwarding=0 >/dev/null
echo "removed. /etc/ppp/options left in place (harmless, empty)."
echo "pf itself left enabled, 'sudo pfctl -d' if you had it off before."
