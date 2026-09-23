#!/bin/sh
# PsionNet, bring the link up in the foreground. Ctrl-C tears it down.
[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }
echo "watching /var/log/ppp-psion.log in another terminal is recommended:"
echo "  tail -f /var/log/ppp-psion.log"
echo
exec /usr/sbin/pppd call psion
