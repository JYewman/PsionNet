#!/bin/sh
# Build psionnet_<version>-1_<arch>.deb from dist/PsionNet, PyInstaller's
# output on Linux. Run from anywhere, on Debian (dpkg-deb), after
#     python3 -m PyInstaller --noconfirm PsionNet.spec
set -e
cd "$(dirname "$0")/../.."
VERSION=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' PsionNet.spec)
ARCH=$(dpkg --print-architecture)
[ -x dist/PsionNet/PsionNet ] || { echo "No dist/PsionNet: run PyInstaller first."; exit 1; }
PKG=build/deb/psionnet_${VERSION}-1_${ARCH}
rm -rf "$PKG"
mkdir -p "$PKG/DEBIAN" "$PKG/opt" "$PKG/usr/bin" "$PKG/usr/share/applications" \
         "$PKG/usr/share/doc/psionnet" "$PKG/usr/lib/psionnet"

cp -a dist/PsionNet "$PKG/opt/psionnet"
ln -s /opt/psionnet/PsionNet "$PKG/usr/bin/psionnet"
install -m 644 packaging/linux/psionnet.desktop "$PKG/usr/share/applications/"
for s in 32 64 512; do
  install -D -m 644 app/icons/psionnet_$s.png "$PKG/usr/share/icons/hicolor/${s}x${s}/apps/psionnet.png"
done

# The serial link: Debian's pppd, and NAT through the ip-up.d hooks.
install -D -m 644 packaging/linux/peers/psion "$PKG/etc/ppp/peers/psion"
install -D -m 644 packaging/linux/peers/psion-ce-modem "$PKG/etc/ppp/peers/psion-ce-modem"
install -D -m 755 packaging/linux/ip-up.d/psionnet "$PKG/etc/ppp/ip-up.d/psionnet"
install -D -m 755 packaging/linux/ip-down.d/psionnet "$PKG/etc/ppp/ip-down.d/psionnet"
install -m 755 etc/ppp/fakemodem.py "$PKG/usr/lib/psionnet/fakemodem.py"
cat > "$PKG/DEBIAN/conffiles" <<CONF
/etc/ppp/peers/psion
/etc/ppp/peers/psion-ce-modem
/etc/ppp/ip-up.d/psionnet
/etc/ppp/ip-down.d/psionnet
CONF

cat > "$PKG/usr/share/doc/psionnet/copyright" <<COPY
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: PsionNet
Source: https://github.com/JYewman/PsionNet

Files: *
Copyright: 2026 Joshua Yewman
License: GPL-2.0-or-later
 PsionNet contains artwork from Reconnect (GPL-2.0-or-later) and icons from
 Psion/Symbian EPOC software; ACKNOWLEDGEMENTS.md in
 /opt/psionnet/_internal/licenses/psionnet has the details. On Debian systems the GPL is in
 /usr/share/common-licenses/GPL-2.

Files: opt/psionnet/_internal/*
License: various
 /opt/psionnet bundles Python (PSF-2.0), Tcl/Tk (BSD-style), Flask, Werkzeug,
 Requests, Beautiful Soup, Pillow and their dependencies (BSD, MIT, Apache-2.0,
 HPND), lameenc with LAME (LGPL-3.0-or-later) and librespot (MIT). Their
 licences are in /opt/psionnet/_internal/licenses and the packages' own
 metadata within /opt/psionnet/_internal.
COPY

SIZE=$(du -sk "$PKG" | cut -f1)
cat > "$PKG/DEBIAN/control" <<CTRL
Package: psionnet
Version: ${VERSION}-1
Architecture: ${ARCH}
Maintainer: Joshua Yewman <joshua@yewman.co.uk>
Installed-Size: ${SIZE}
Depends: libc6 (>= 2.36), libx11-6, libxft2, libxss1, libfontconfig1
Recommends: ppp, nftables, pkexec | policykit-1, python3
Section: net
Priority: optional
Homepage: https://github.com/JYewman/PsionNet
Description: Internet for Psion handhelds and the netBook Pro
 PsionNet puts a Psion Series 5mx, Series 7, netBook, Revo or netBook Pro on
 the modern internet: a PPP link over the serial cable, and a proxy that turns
 modern web pages into HTML the Psion's browser can read. A netBook Pro on the
 network needs only the proxy. For PsionLX it is also a Spotify speaker and the
 software library behind "Find new software".
 .
 The serial link uses ppp; a user in the groups dip and dialout connects
 without a password.
CTRL

dpkg-deb --build --root-owner-group "$PKG" build/deb/ >/dev/null
echo "built build/deb/psionnet_${VERSION}-1_${ARCH}.deb ($(du -h build/deb/psionnet_${VERSION}-1_${ARCH}.deb | cut -f1))"
