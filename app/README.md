# PsionNet control panel

One window for PsionNet: the PPP link, the downgrading proxy, and the
netBook Pro's Spotify.

## Supported devices

Picked from the **Device** menu:

| Device | How it connects |
|---|---|
| Series 5mx / 7 / netBook / Revo | serial cable, PPP direct link, 115200 |
| netBook Pro (CE, dial-up) | serial cable, dial-up to an emulated modem, 19200 |
| netBook Pro (CE, network) | its own network card; no link to bring up |
| netBook Pro (PsionLX, network) | its own network card; adds Spotify |

The **Psion netBook Pro** runs Windows CE 4.2 .NET rather than EPOC. Over the
serial cable it connects as **dial-up to an emulated modem** at 19200 baud
rather than as a direct link, so it has its own peer profile. Its Direct
Connection mode was tried and removed: PPP negotiates fully and then carries
no traffic, because DCC binds to the sync stack rather than TCP/IP.

On a network the netBook Pro needs no link at all, whether it runs Windows CE
or **PsionLX**, Psion's Linux. See [Network mode](#network-mode).

Everything else here is **EPOC Release 5**: the **Series 5mx**, **Series 7**,
**netBook** and **Revo**/Revo Plus. ER5 ships the same TCP/IP and PPP stack
across all of them, and the same "Web 2" browser, in ROM on the Series 7 and
a separate install elsewhere. Eric Lindsay's EPOC pages put it plainly:
"Epoc Release 5 (Psion5mx, Revo and all other models) provides Web 2 ... (it
is in ROM on the Series 7)."

That means the proxy's HTML 3.2 output, derived by reading the Series 7 ROM's
own parser tables, applies unchanged to the other ER5 machines.

The **Series 5 classic is not ER5**; it runs EPOC Release 3, a different OS
release with a different connectivity story. It may well work, but nothing
here has been verified against it, so it is not claimed.

The Psion-side settings are documented for the Series 7 in the main
[README](../README.md#psion-side). On a 5mx the same dialogs exist but are
reached as **Remote link** rather than **Link to desktop**, and the browser
may need installing from the PsiWin CD rather than being present in ROM.

**Double-click `PsionNet.app`.** That is the whole of it: no terminal, no
install step, no preflight. Drag it to /Applications if you like; it keeps
working.

To rebuild it after changing the code:

```sh
sh bin/build-app.sh
```

Or run the GUI straight from source with `python3 app/gui.py`.

### How the bundle works

Built with PyInstaller, which bundles the interpreter, the stdlib, Tk and
every dependency, and librespot for Spotify. **76 MB, and it runs on a Mac
with no Python installed.**
verified by launching it in a stripped environment with nothing but
`/usr/bin:/bin` on `PATH`.

Two things made this work:

1. **The app re-launches itself to run the proxy.** There is no separate
   `python3` to call once frozen, so `control.proxy_argv` spawns
   `sys.executable --run-proxy` and `app/main.py` dispatches on that flag.
   Same binary, two modes.
2. **Paths resolve through `sys._MEIPASS` when frozen.** Nothing points
   outside the bundle, and nothing is written to Application Support at build
   time, so the .app is genuinely copyable.

PyInstaller 6.22.3 supports Python 3.14; py2app does not.

### Giving it to someone else

Copy the .app across. It is **signed with a Developer ID and notarised by
Apple**, with the ticket stapled, so it opens on any Mac with no warning and
no right-click dance. `bin/sign-app.sh --notarize` does the signing,
submission and stapling.

One wrinkle worth recording: `Contents/Frameworks/Tcl` and `Tk` are bare
Mach-O binaries with no file extension, so a `*.so`/`*.dylib` glob skips them
and notarisation comes back Invalid. The script detects Mach-O with `file`
rather than by extension.

### First run

The app installs its own PPP configuration, including the peer files for both
device families and the fake modem responder the netBook Pro dials. The first
time you press **Connect**, it asks whether to write `/etc/ppp/peers/psion`,
`/etc/ppp/peers/psion-ce-modem`, `/etc/ppp/fakemodem.py`, `/etc/ppp/ip-up`,
`/etc/ppp/ip-down` and `/etc/pf.anchors/psion.nat`, then authenticates once.
It also removes the files an earlier version installed for the Direct
Connection mode, if they are PsionNet's. `bin/preflight.sh` and
`bin/install.sh` are still there for terminal use, but nothing requires them.

The fake modem runs under `/usr/bin/python3`, which macOS provides once the
Command Line Tools are installed (`xcode-select --install`). It logs to
`/var/log/psionnet-fakemodem.log`.

## What it does

- **Serial port**: lists every `/dev/cu.*`, marks the one that looks like a USB
  serial adapter, and shows which process is holding it. If Reconnect has the
  port, it says so and offers to quit it.
- **Connection**: starts and stops `pppd`, shows the negotiation stage
  (LCP → IPCP → up), the assigned addresses, and live throughput.
- **Web proxy**: starts and stops the proxy, shows its address, requests
  served, errors and in-flight count. Detail level and images are switchable.
- **Logs**: the pppd log and the proxy's own output, in tabs.
- **Spotify** (PsionLX, network only): one line saying how the *netBook Pro*
  speaker is doing; click it to unfold your Spotify developer Client ID and
  the Log in / Log out button. It remembers whether you left it open.

## Network mode

For a netBook Pro on the network there is no serial port and no PPP. The
**Listen on** row offers each of the Mac's private IPv4 addresses (VPN, PPP and
bridge interfaces are left out), and the proxy is started on the one chosen,
port 8080, with that interface's subnet as the only network allowed to use it
besides the Mac itself. Anything else gets a 403.

The proxy also answers a UDP broadcast, `PSIONNET?` on port 8899, with
`PSIONNET <address> <port>`, which is how PsionLX's Spotify app, "Find new
software" and its login helper find it without being told an address. It
answers only the same subnet.

For *netBook Pro (PsionLX, network)* the proxy is also started with
`--software`, and the status line shows the one command that sets a PsionLX
card up: `wget -O - http://<address>:8080/lx/install | sh`, as root.

## Spotify

The Spotify box appears for *netBook Pro (PsionLX, network)* only.

**Log in** runs Spotify's PKCE sign-in in your browser, against your own
Client ID, with a redirect to `http://127.0.0.1:8897/callback` that a small
server in the app catches. No client secret is involved. The token is saved to
`~/Library/Application Support/PsionNet/spotify-token.json`, mode 0600, and
refreshed as it expires. **Log out** deletes it and librespot's cached
credentials.

When the proxy starts in this mode it is started with `--spotify`, which
brings up librespot (bundled in the app) as the *netBook Pro* speaker. The
speaker signs in separately, once: librespot opens Spotify's page in the
browser, because Spotify will not accept the Client ID login for a speaker. The
proxy writes its state to `spotify-status.json` in the same folder every few
seconds, and the box shows it.

## Privilege

`pppd` needs root; the proxy does not. Rather than run the whole GUI as root,
only the pppd invocation is elevated, through the standard macOS
authorisation dialog (on Linux, polkit's, or none at all: see below).

Nothing persistent is installed: no `sudoers` entry, no LaunchDaemon, no
setuid helper. The cost is a password prompt when starting or stopping the
link. If that becomes annoying, a `/etc/sudoers.d` rule scoped to just `pppd`
and `pfctl` would remove it, at the price of standing root access for those
two commands.

## Windows and Linux

The same code runs on all three. What differs is decided in one place,
`proxy/psionproxy/host.py`: where files live (`~/Library` on a Mac, XDG folders
on Linux, `%APPDATA%` on Windows), what messages call the computer, and how a
child process is started without a window of its own.

* **Linux** has the serial link. Ports come from `/dev` (`ttyUSB`, `ttyACM`,
  and `ttyS` only where a UART answers), named from `/dev/serial/by-id`; who
  holds a port is read from `/proc`; the link's addresses through ioctls and
  its byte counts from sysfs. `pppd` runs as the user when the user is in the
  groups `dip` and `dialout`, as Debian intends, and otherwise through
  `pkexec`. Its log goes to `~/.local/state/psionnet/ppp.log`, a file the user
  owns, as `pppd` run by a user may write nowhere else.
* **Windows** has no `pppd`, so the Device menu offers only the netBook Pro on
  the network, and the serial port and Connect are hidden. The proxy is
  stopped as a whole process tree (`taskkill /T`), which takes librespot with
  it; Windows has no process groups to signal.

On every system, stopping the proxy stops librespot. It runs in a session of
its own, and before 1.3 each proxy restart left one running: a *netBook Pro*
speaker with nothing behind it.

## Why Tkinter

It is the only GUI toolkit present on this machine (Tk 8.6, via the stdlib).
No PyQt, PySide, wx or pyobjc are installed, so Tkinter means the app runs
with zero additional dependencies. Tk 8.6 also reads PNG natively, so the
icons need no Pillow.

## Icons

The Psion clamshell and the EPOC application icons come from
[Reconnect](https://github.com/inseven/reconnect) (GPL-2.0-or-later). The blue
PsionNet icon is a hue rotation of Reconnect's, so the two are distinguishable
in the Dock.

`psion.png` and `word.png` are macOS *template* images (LA mode), which the OS
expects to tint. Tk does not, so `*_tinted.png` variants are generated with the
alpha channel preserved as a mask.

Full attribution, and the record of modifications GPLv2 §2(a) requires, is in
[ACKNOWLEDGEMENTS.md](../ACKNOWLEDGEMENTS.md).

## Threading

Tk is not thread-safe, so no widget is touched from a worker thread. Child
process output goes into `queue.Queue`s which the main thread drains on an
`after()` tick. `pppd` is started detached under `osascript`, so its output is
read by tailing `/var/log/ppp-psion.log` rather than from a pipe.
