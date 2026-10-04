"""Tunables for the Psion downgrading proxy.

Byte budgets all divide by an assumed ~11 KB/s of PPP payload at 115200 baud.
Measure a real transfer on day one and recalibrate BUDGET_* if it differs.
"""

BIND_HOST = "10.0.2.1"   # the Mac's PPP-side address.
                         # Never bind 0.0.0.0: this is an open forward proxy that
                         # terminates TLS, and on a shared network that offers the
                         # service to everyone on it.
BIND_PORT = 8080

# --- network (LAN) mode --------------------------------------------------------
# A serial device reaches the proxy over its own PPP link, where nothing else
# can. A netBook Pro with a network card is on the LAN instead, so the proxy
# binds the Mac's LAN address -- which every machine on that network can reach.
# ALLOWED_NETS limits who may use it: loopback always, plus these networks.
# Empty means no restriction, which is only right for the PPP address.
ALLOWED_NETS: list = []        # ipaddress.IPv4Network objects
SPOTIFY = False                # serve the Spotify bridge for the PsionLX app
SPOTIFY_DEMO = False           # canned results and a test tone, for testing
DISCOVERY_PORT = 8899          # UDP: the PsionLX app asks "PSIONNET?" here
SOFTWARE = False               # serve PsionLX-Software, for "Find new software"
SOFTWARE_SRC = "https://archive.retrotechcollection.com/PsionLX-Software"


def apply_software_args(enabled: bool, src: str = "") -> None:
    """--software, and --software-src (a URL, or a local folder for testing)."""
    global SOFTWARE, SOFTWARE_SRC
    SOFTWARE = bool(enabled or src)
    if src:
        SOFTWARE_SRC = src


def apply_network_args(allow: str, spotify: bool, demo: bool) -> None:
    """Shared by run.py and the app's --run-proxy path."""
    import ipaddress
    global ALLOWED_NETS, SPOTIFY, SPOTIFY_DEMO
    nets = []
    for part in (allow or "").split(","):
        part = part.strip()
        if part:
            nets.append(ipaddress.ip_network(part, strict=False))
    ALLOWED_NETS = nets
    SPOTIFY = bool(spotify)
    SPOTIFY_DEMO = bool(demo)

# --- byte budgets -----------------------------------------------------------
# Raised from 20k/24k. Wire time says 110 KB is a 10 s page, but Psion-side
# RENDER time is unmeasured, so these sit deliberately below the wire ceiling
# until the on-device bench at http://psion/bench/ says otherwise.
BUDGET_HTML_TARGET = 40_000   # start shedding past this
BUDGET_HTML_HARD = 64_000     # never exceed
BUDGET_IMAGE = 16_000         # per transcoded image
BUDGET_IMAGES_PER_PAGE = 12
# Images are separate requests, so they escape BUDGET_HTML_HARD entirely --
# BBC News measured 42 KB of HTML plus 155 KB of images = 19 s. The real
# ceiling is the WHOLE page, so inlined images get whatever the HTML leaves.
BUDGET_PAGE_TOTAL = 130_000   # ~12.6 s at the measured 10,335 B/s.
                              # Confirmed acceptable on real hardware: pages
                              # of this weight (BBC 11.0 s, Ars 12.2 s wire)
                              # load fine on the Series 7, so StrongARM
                              # layout time is NOT the binding constraint --
                              # the serial link is.
IMAGE_SIZE_ESTIMATE = 10_000  # measured mean of a transcoded 400 px image
BUDGET_DEFERRED_LINKS = 15    # the links row is not free: 91 Guardian
                              # image URLs came to 22 KB on their own
BUDGET_UPSTREAM_MAX = 4_000_000  # the Guardian front page alone is 1.5 MB

# --- image transcoding ------------------------------------------------------
IMAGE_LONG_EDGE = 400         # default; hard ceiling below
IMAGE_LONG_EDGE_MAX = 500     # measured content viewport is ~546x430
IMAGES_DEFAULT_ON = True      # inline images; --no-images defers them to links

# --- rendering fidelity ------------------------------------------------------
# "lite"   = readability extraction only (the old behaviour; smallest pages)
# "medium" = whole body, pruned and cleaned, no extraction  <- default
# "full"   = as medium, but with a larger budget and nothing shed
FIDELITY = "medium"
RELATIVISE_URLS = True        # same-origin links -> relative. Lossless, ~7-22%.

# --- upstream ---------------------------------------------------------------
UPSTREAM_UA = "PsionProxy/1.0"
# Wikimedia asks for a descriptive agent; anything Chrome-like gets challenged by DDG.
UPSTREAM_UA_WIKIMEDIA = "PsionProxy/1.0 (Psion Series 7 text gateway)"
UPSTREAM_TIMEOUT = (5, 20)    # (connect, read)

# Hosts that only serve cleanly over plaintext (mismatched certs, but port 80 is fine).
HTTP_ONLY_HOSTS = {
    "frogfind.com", "www.frogfind.com",
    "68k.news", "www.68k.news",
}

# Known-hostile or pointless: fail fast rather than making the Psion wait.
DENY_HOSTS = {
    # hard 403/202 regardless of user agent
    "stackoverflow.com", "nytimes.com", "medium.com", "ebay.co.uk",
    "quora.com", "indeed.com", "g2.com", "imdb.com", "washingtonpost.com",
    # 200 but empty JavaScript shells
    "x.com", "twitter.com", "linkedin.com", "instagram.com", "facebook.com",
}

# --- search -----------------------------------------------------------------
SEARCH_CACHE_SECONDS = 900
DDG_MIN_INTERVAL = 90         # seconds between DuckDuckGo hits; it bans fast repeats
DDG_BLOCK_COOLDOWN = 600      # a 202 from DDG is sticky, not transient
