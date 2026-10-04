# Acknowledgements and third-party material

PsionNet is Copyright (C) 2026 Joshua Yeaman and is licensed under the GNU
General Public License, version 2 or later (`SPDX-License-Identifier:
GPL-2.0-or-later`). See [LICENSE](LICENSE).

PsionNet is under the GPL because it contains and derives from artwork taken
from Reconnect. That is the reason for the licence choice, and it is stated
here rather than buried.

PsionNet is not affiliated with Reconnect, Psion, Symbian, Spotify or Apple,
and is not endorsed by any of them. Spotify is a trademark of Spotify AB.

## Reconnect

    Reconnect: Psion connectivity for macOS
    Copyright (C) 2024-2026 Jason Morley
    https://github.com/inseven/reconnect
    GNU General Public License, version 2 or later

Reconnect is also the reason this project exists. It is what first got a
Psion talking to a modern Mac.

### Unmodified copies of files from Reconnect

    app/icons/disconnected.png
    app/icons/drive.png
    app/icons/psion.png
    app/icons/sketch.png

### PsionNet's modifications of Reconnect artwork

GPLv2 §2(a) requires modified files to carry notice of what changed and when.
A PNG cannot carry that itself, so it is recorded here. Each of the following
recolours Jason Morley's drawing of a Psion Series 5. No outline, shape or
detail has been altered.

* `app/icons/psionnet_{32,64,512}.png`, derived in 2026 from Reconnect's
  application icon by rotating every hue, orange to blue, so the two
  applications are distinguishable in the Dock. Geometry unchanged.
* `app/icons/psion_tinted.png`, `app/icons/word_tinted.png`, derived in 2026
  by tinting the greyscale template images. Geometry and alpha unchanged.
* `proxy/psionproxy/assets/psionnet_{32,48,64}.gif`, derived in 2026 from
  `psionnet_512.png` by resampling and conversion to non-interlaced GIF87a,
  for the pages served to the Psion.
* `build_icon/PsionNet.icns` (build output, not checked in), generated from
  `psionnet_512.png` by `bin/build-app.sh`.

The vector source for the drawing is in Reconnect's repository.

## Psion / Symbian artwork

PsionNet contains device and application icons that originate in Psion's own
EPOC software and in the PsiWin and PsiMac desktop software for the Series 5,
5mx, 7, netBook and Revo. They reached PsionNet by way of Reconnect, which
states of them: "Reconnect includes graphics (icons and animations) from the
original Psion PsiWin and PsiMac software. These remain copyright Psion PLC."

Psion and Symbian are trademarks of their respective owners.

## Sources consulted

The proxy's HTML target was derived by reading the Series 7 ROM's own parser
tables. Documentation and measurements also draw on:

* The *Psion Series 7 User Guide* (Psion PLC)
* The Psion Series 7 FAQ, and Eric Lindsay's EPOC pages
* Symbian's "EPOC does Unicode" paper (IUC16)

## Python dependencies

Not vendored. They are installed from `proxy/requirements.txt`, or bundled into
`PsionNet.app` by PyInstaller. Each carries its own licence.

| Package | Licence |
|---|---|
| Flask, Werkzeug, Jinja2, click, itsdangerous, MarkupSafe | BSD-3-Clause |
| requests | Apache-2.0 |
| beautifulsoup4, soupsieve, urllib3, charset-normalizer | MIT |
| Pillow | MIT-CMU (HPND-style) |
| certifi | MPL-2.0 |
| httpx (optional) | BSD-3-Clause |
| lameenc, which contains LAME | LGPL-3.0-or-later (LAME itself LGPL-2.0-or-later) |

lameenc is loaded as a separate shared module, so it can be replaced in the
built app. PsionNet's own code is GPL-2.0-or-later; combined with an
LGPL-3.0 library, the distributed app as a whole falls under GPL-3.0-or-later
terms, which "or later" permits.

## librespot

    librespot: an open-source client library for Spotify
    Copyright (c) 2015 Paul Lietar, and the librespot contributors
    https://github.com/librespot-org/librespot
    MIT License

`PsionNet.app` bundles the `librespot` program, unmodified, as built by
Homebrew, and runs it as a separate process: it is the *netBook Pro* Spotify
speaker. Its licence travels with it in the app, at
`Contents/Resources/licenses/librespot/LICENSE`, beside lameenc's. It is
statically linked with the Rust libraries it is built from, each under its own
licence; librespot's `Cargo.lock` lists them.
