# PsionNet control panel

One window for both halves of PsionNet: the PPP link and the downgrading proxy.

## Supported devices

Anything running **EPOC Release 5**: the **Series 5mx**, **Series 7**,
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
every dependency. **57 MB, and it runs on a Mac with no Python installed.**
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

Copy the .app across. It is **ad-hoc signed, not notarised**, so Gatekeeper
asks once on the receiving Mac:

- right-click the app and choose **Open**, then **Open** again, or
- `xattr -dr com.apple.quarantine /path/to/PsionNet.app`

Notarising it properly would need a paid Apple Developer ID, which is
disproportionate here.

### First run

The app installs its own PPP configuration. The first time you press
**Connect**, it asks whether to write `/etc/ppp/peers/psion`,
`/etc/ppp/ip-up`, `/etc/ppp/ip-down` and `/etc/pf.anchors/psion.nat`, then
authenticates once. `bin/preflight.sh` and `bin/install.sh` are still there
for terminal use, but nothing requires them.

## What it does

- **Serial port**: lists every `/dev/cu.*`, marks the one that looks like a USB
  serial adapter, and shows which process is holding it. If Reconnect has the
  port, it says so and offers to quit it.
- **Connection**: starts and stops `pppd`, shows the negotiation stage
  (LCP → IPCP → up), the assigned addresses, and live throughput.
- **Web proxy**: starts and stops the proxy, shows its address, requests
  served, errors and in-flight count. Detail level and images are switchable.
- **Logs**: the pppd log and the proxy's own output, in tabs.

## Privilege

`pppd` needs root; the proxy does not. Rather than run the whole GUI as root,
only the pppd invocation is elevated, through the standard macOS
authorisation dialog.

Nothing persistent is installed: no `sudoers` entry, no LaunchDaemon, no
setuid helper. The cost is a password prompt when starting or stopping the
link. If that becomes annoying, a `/etc/sudoers.d` rule scoped to just `pppd`
and `pfctl` would remove it, at the price of standing root access for those
two commands.

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
