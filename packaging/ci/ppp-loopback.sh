#!/bin/sh
# The Linux serial link end to end, without a Psion: a pty pair stands in for
# the cable and a network namespace for the Psion. PsionNet's side is started
# by the app's own control.ppp_start(), with the installed package's peer
# files and NAT hooks; the "Psion" is a second pppd in the namespace, which
# then browses through the proxy and reaches the internet through the NAT.
#
#     sudo -E sh packaging/ci/ppp-loopback.sh epoc|ce
#
# Needs: the psionnet package installed, socat, curl, and root.
set -u
KIND=${1:-epoc}
HERE=$(cd "$(dirname "$0")/../.." && pwd)
FAIL=0
ok() { if [ "$1" = 0 ]; then echo "[PASS] $2"; else echo "[FAIL] $2"; FAIL=1; fi; }
py() { python3 -c "import sys; sys.path.insert(0, '$HERE/app'); $1"; }

FWD_BEFORE=$(cat /proc/sys/net/ipv4/ip_forward)
socat pty,raw,echo=0,link=/tmp/psionnet-host pty,raw,echo=0,link=/tmp/psionnet-psion &
SOCAT=$!
sleep 1
ip netns add psion

py "import control; print('ppp_start:', control.ppp_start('/tmp/psionnet-host', '$KIND'))"
if [ "$KIND" = ce ]; then
  # A netBook Pro dialling a modem: AT commands first, then PPP at 19200.
  ip netns exec psion pppd /tmp/psionnet-psion 19200 noauth local nodetach noipdefault \
    defaultroute crtscts asyncmap 0 logfile /tmp/psion-side.log debug \
    connect "chat -v '' ATZ OK ATDT123 CONNECT" &
else
  ip netns exec psion pppd /tmp/psionnet-psion 115200 noauth local nodetach noipdefault \
    defaultroute crtscts asyncmap 0 logfile /tmp/psion-side.log debug &
fi
PSION=$!

UP=1
for i in $(seq 1 40); do
  if py "import probe; s = probe.link_state(); sys.exit(0 if s.up and s.local_ip == '10.0.2.1' else 1)"; then UP=0; break; fi
  sleep 1
done
ok $UP "the link comes up, as the app's probe sees it (10.0.2.1 to 10.0.2.2)"
py "import probe; print('   ', probe.link_state())"

/opt/psionnet/PsionNet --run-proxy --host 10.0.2.1 --port 8080 > /tmp/psionnet-proxy.log 2>&1 &
PROXY=$!
for i in $(seq 1 30); do curl -s -o /dev/null -m 2 http://10.0.2.1:8080/ && break; sleep 1; done

ip netns exec psion curl -s -m 30 -x http://10.0.2.1:8080 http://psion/ | grep -qi psionnet
ok $? "the Psion browses through the proxy, over the link"
CODE=$(ip netns exec psion curl -s -m 30 -o /dev/null -w '%{http_code}' http://1.1.1.1/)
case "$CODE" in 2*|3*) R=0 ;; *) R=1 ;; esac
ok $R "the Psion reaches the internet through the NAT (http://1.1.1.1/ answered $CODE)"
nft list table ip psionnet >/dev/null 2>&1 || iptables -t nat -S POSTROUTING | grep -q 10.0.2.0/30
ok $? "ip-up.d/psionnet put the NAT in place"

py "import control; print('ppp_stop:', control.ppp_stop())"
sleep 3
! pgrep -f '^/usr/sbin/pppd call psion' >/dev/null
ok $? "Disconnect stops pppd"
! nft list table ip psionnet >/dev/null 2>&1
ok $? "ip-down.d/psionnet took the NAT away"
[ "$(cat /proc/sys/net/ipv4/ip_forward)" = "$FWD_BEFORE" ]
ok $? "IP forwarding is back as it was ($FWD_BEFORE)"

kill $PROXY $PSION $SOCAT 2>/dev/null
ip netns del psion
echo "--- PsionNet's pppd log"; tail -25 "$(py "import control; print(control.PPP_LOG_LINUX)")" 2>/dev/null
echo "--- the Psion's pppd log"; tail -15 /tmp/psion-side.log 2>/dev/null
[ "$KIND" = ce ] && { echo "--- the fake modem"; tail -12 /var/log/psionnet-fakemodem.log 2>/dev/null; }
exit $FAIL
