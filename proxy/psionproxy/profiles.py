"""Client profiles: what each device can actually render.

The EPOC profile was derived by reading the Series 7 ROM image directly -- its
element table, entity table and registered content decoders. The Windows CE
profile was derived empirically, by serving a capability probe to a real Psion
netBook Pro and recording what it drew.

Probe results from a netBook Pro (Mozilla/4.0 compatible; MSIE 6.0; Windows CE):

    GIF                 rendered
    baseline JPEG       rendered
    PNG                 DID NOT RENDER    -> still transcode images
    HTML entities       rendered
    raw UTF-8 bytes     rendered          -> no ISO-8859-1 flattening
    CSS style attribute rendered          -> keep inline styles
    CSS style block     rendered          -> keep <style> blocks

So CE needs the same image handling as EPOC and almost nothing else: it wants
TLS terminated, and then to be left alone.

The PsionLX profile is the same netBook Pro running Linux: Firefox 1.0, which
renders PNG and far more CSS than Pocket IE but still predates everything a
modern stylesheet is made of, and has TLS 1.0 at best. It reaches the proxy
over the LAN rather than a serial link.
"""

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Profile:
    key: str
    name: str
    charset: str
    downgrade_text: bool      # flatten to the CP1252/ISO-8859-1 intersection
    keep_css: bool            # preserve <style>, style=, <link rel=stylesheet>
    strict_html32: bool       # restrict to the ROM's element/attribute tables
    doctype: str
    budget_html_hard: int
    budget_css: int = 0       # bytes of filtered CSS worth carrying


EPOC = Profile(
    key="epoc",
    name="Psion Series 5mx / 7 / netBook / Revo (EPOC ER5)",
    charset="iso-8859-1",
    downgrade_text=True,
    keep_css=False,
    strict_html32=True,
    doctype='<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">',
    budget_html_hard=64_000,
    budget_css=0,             # no CSS engine in the ROM at all
)

CE = Profile(
    key="ce",
    name="Psion netBook Pro (Windows CE, Pocket IE)",
    charset="utf-8",
    downgrade_text=False,
    keep_css=True,
    strict_html32=False,
    doctype='<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">',
    # The CE link runs at 19200, about 1.9 KB/s, a fifth of the Series 7's.
    # These are deliberately generous: a fuller page is worth the wait on this
    # device, and the two passes that matter (dead class removal and CSS
    # filtering) cut far more than the budget ever has to.
    budget_html_hard=192_000,      # ~100 s of link time
    budget_css=48_000,             # ~25 s, after filter_for_ie6 has run
)

LX = Profile(
    key="lx",
    name="Psion netBook Pro (PsionLX, Firefox 1.0)",
    charset="utf-8",
    downgrade_text=False,
    keep_css=True,
    strict_html32=False,
    doctype='<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">',
    # Gecko 1.7 renders far more CSS than Pocket IE, but not the parts modern
    # pages are built from -- no flex, grid, calc(), rem or custom properties
    # -- so the same IE6-subset filter is the right one. The link is a LAN, not
    # a 19200 modem, so the budgets are set by what a 400 MHz XScale lays out
    # comfortably rather than by wire time.
    budget_html_hard=400_000,
    budget_css=96_000,
)

ALL = {p.key: p for p in (EPOC, CE, LX)}
DEFAULT = EPOC

# Firefox 1.0 / Gecko 1.7 as PsionLX ships it. Deliberately narrow: a modern
# Firefox says "Firefox/1xx.0" and must not match "Firefox/1.".
_LX_UA = re.compile(r"\bfirefox/1\.\d(?![\d])|\brv:1\.[0-8](\.\d+)*\) gecko/")


def for_user_agent(ua: str) -> Profile:
    """Pick a profile from the client's User-Agent.

    Defaults to EPOC: it is the stricter output, and strict output is valid
    input for a more capable browser. Guessing wrong in that direction costs
    fidelity; guessing wrong the other way produces a page the device cannot
    render at all.
    """
    if not ua:
        return DEFAULT
    low = ua.lower()
    if "windows ce" in low or "pocket" in low or "wince" in low:
        return CE
    if "epoc" in low or "stnc-wtl" in low:
        return EPOC
    if _LX_UA.search(low):
        return LX
    return DEFAULT
