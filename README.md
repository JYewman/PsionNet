# PsionNet

Put a **Psion** on the modern internet, from a Mac, over the serial cable.

PsionNet does two things. It brings up a PPP link over RS-232 so the Psion gets
a real IP address and routes through your Mac, and it runs a proxy that
terminates modern TLS and rewrites pages into the HTML 3.2 subset the device's
1999 browser can actually parse.

Both halves ship as one notarised macOS app. No terminal required.

<p align="center">
  <img src="docs/screenshot.png" alt="The PsionNet control panel" width="640">
</p>

> **Status:** works, and used in anger. Developed against a Series 7; the link,
> the proxy and the app are all verified end to end on real hardware.

---

## Why this needs to exist

EPOC Release 5 shipped a complete TCP/IP and PPP stack, so a Psion from 1999
can still get online. The networking was never the problem; the web was.

The ROM browser has **no SSL at all**. The string `https` does not appear once
in the entire 16 MB ROM, so there is no scheme handler to fix and nothing to
negotiate. It has no CSS engine, no JavaScript, no PNG decoder, no gzip, and an
ISO-8859-1 character set. In 2026 that means essentially nothing on the public
web will load.

The proxy does the TLS on the Mac and hands the Psion plain HTTP that it can
render, so an unmodified machine browses the real internet.

## What you need

**Hardware**

* A Psion running **EPOC Release 5**: Series 5mx, Series 7, netBook, or Revo.
  (The Series 5 *classic* runs ER3 and is not covered.)
* The Psion's own serial cable. It is already wired as a null modem, so no
  crossover adapter is needed.
* A USB-to-RS-232 adapter. Anything with a working macOS driver: FTDI, PL2303,
  CH34x, or an ATEN UC-232A.

**Software**

* macOS 11 or later, Apple Silicon or Intel.
* Nothing else. `PsionNet.app` bundles its own Python, Tk and dependencies.

To run from source instead, you need Python 3.10+ with Tk, and
`pip install -r proxy/requirements.txt`.

## Getting started

1. **Open PsionNet.app.**
2. Pick the serial port. The app marks the one that looks like a USB adapter
   and tells you if something else is holding it.
3. Press **Connect**. The first time, it offers to install the PPP
   configuration and asks for your password once.
4. On the Psion:
   * System → Tools → **Link to desktop** (or **Remote link**) → set **Link =
     Off**, so the port is free.
   * Control panel → **Dialling** → set a location. Nothing is dialled, but
     skipping this gives *"Connection information not found"*.
   * Control panel → **Modems** → choose **Direct cable connection**, 115200,
     hardware (RTS/CTS) flow control.
   * Control panel → **Internet** → new service, **Connection type: Direct**.
     Tick *Get IP address from server* and *Get DNS address from server*.
5. Open **Web** on the Psion to bring the link up.
6. Start the proxy in the app, then on the Psion set
   Web → Tools → **Proxy server settings** → `10.0.2.1`, port `8080`.
7. Browse to `http://psion/` for the home page and search.

## How it works

### The link

`pppd`, which macOS still ships, talks PPP over the serial line. The Psion's
ROM has a **Direct cable connection** modem profile and a *Connection type:
Direct* option, so there is no modem handshake to emulate: it opens the port and
starts LCP.

The Mac NATs the link out through its own uplink with a `pf` anchor, loaded into
a child anchor under Apple's existing hooks so `/etc/pf.conf` is never touched.

Serial `pppd` on Apple Silicon appears to be undocumented territory. No prior
report of it working turned up anywhere. It does work.

### The proxy

Requests arrive as ordinary HTTP. The proxy fetches over modern TLS, then:

* **prunes hidden markup**: `hidden`, `aria-hidden`, inline `display:none`.
  On BBC News that is 52% of the page, mostly four near-identical copies of the
  same story block shipped for different screen widths
* **rewrites to HTML 3.2**, against element and attribute whitelists read out of
  the Series 7 ROM's own parser tables
* **transcodes images** to non-interlaced GIF or baseline JPEG, the only two
  formats the ROM can decode
* **downgrades text** to the byte range where CP1252 and ISO-8859-1 agree
* **relativises same-origin URLs**. `href`s are 26-48% of output bytes

| Page | Upstream | To the Psion | Wire time |
|---|---|---|---|
| BBC News | 1,041 KB | 31 KB | 3.0 s |
| The Guardian | 1,496 KB | 48 KB | 4.7 s |
| Wikipedia article | 100 KB | 24 KB | 2.3 s |
| Hacker News | 34 KB | 19 KB | 1.8 s |

Measured link throughput is **10,650 octets/s**, so roughly 100 KB per ten
seconds.

## What does not work

Being honest about the limits:

* **Google returns nothing.** Google Search has required JavaScript since
  January 2025. Every endpoint returns about 91 KB of script and roughly 180
  characters of visible text. `google.com` is intercepted and a working search
  page is served instead, backed by DuckDuckGo, Marginalia and wiby.
* **Google will never look like Google.** Its identity is entirely CSS, and
  there is no CSS engine. Pages render as structured documents, not designs.
* **Forms work; POST does not.** Search boxes submit as GET.
* **No JavaScript**, ever.
* Some sites refuse proxied requests outright and are short-circuited rather
  than left to time out.

Browsing is a charming demo. The genuinely useful thing is that a Series 7 is an
excellent VT100 with a full-size keyboard and all-day battery.

## Development

```sh
python3 app/main.py              # run the GUI from source
python3 proxy/run.py --host 127.0.0.1   # run just the proxy, test from this Mac

python3 proxy/tests/test_units.py    # transforms, offline
python3 proxy/tests/test_wire.py     # end-to-end, hits the network
python3 proxy/tests/test_startup.py  # launches run.py for real

sh bin/build-app.sh              # build PsionNet.app with PyInstaller
sh bin/sign-app.sh --notarize    # sign and notarise (needs your own Apple ID)
```

`test_wire.py` drives the proxy with the exact bytes a Series 7 sends
absolute-URI request line, `User-Agent: EPOC32-WTL/2.0 (VGA)`, empty
`Accept-Encoding`. It asserts the reply is HTTP/1.0, unchunked, uncompressed,
`charset=iso-8859-1`, free of C1 bytes, free of `https://`, and free of any tag
outside the ROM's element table.

More detail in [app/README.md](app/README.md) and
[proxy/README.md](proxy/README.md).

### A note on privilege

`pppd` needs root; the proxy does not. Only the `pppd` invocation is elevated,
through the standard macOS authorisation dialog. Nothing persistent is
installed: no `sudoers` entry, no `LaunchDaemon`, no setuid helper. The cost is
a password prompt on connect and disconnect.

Installing the PPP configuration writes `/etc/ppp/peers/psion`,
`/etc/ppp/ip-up`, `/etc/ppp/ip-down` and `/etc/pf.anchors/psion.nat`.
**`ip-up` and `ip-down` are global hooks** that pppd runs for every PPP session
on the machine, so both begin by checking for this project's own `ipparam` tag
and exiting immediately for anything else. `bin/uninstall.sh` removes the lot.

### Security

The proxy is an open forward proxy that strips TLS. It binds the PPP address,
or loopback when there is no link. **Never `0.0.0.0`.** It also refuses to
fetch private, loopback or link-local addresses, so it cannot be used to reach
inside the network it runs on.

## Licence

[GPL-2.0-or-later](LICENSE). PsionNet contains artwork derived from
[Reconnect](https://github.com/inseven/reconnect), which is GPL-2.0-or-later,
and icons from Psion/Symbian EPOC software.

See [ACKNOWLEDGEMENTS.md](ACKNOWLEDGEMENTS.md) for full attribution.
