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
"""

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


EPOC = Profile(
    key="epoc",
    name="Psion Series 5mx / 7 / netBook / Revo (EPOC ER5)",
    charset="iso-8859-1",
    downgrade_text=True,
    keep_css=False,
    strict_html32=True,
    doctype='<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">',
    budget_html_hard=64_000,
)

CE = Profile(
    key="ce",
    name="Psion netBook Pro (Windows CE, Pocket IE)",
    charset="utf-8",
    downgrade_text=False,
    keep_css=True,
    strict_html32=False,
    doctype='<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">',
    # The CE link runs at 19200 (~1.9 KB/s), a fifth of the Series 7's, so the
    # budget is smaller in bytes even though the device is more capable.
    budget_html_hard=32_000,
)

ALL = {p.key: p for p in (EPOC, CE)}
DEFAULT = EPOC


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
    return DEFAULT
