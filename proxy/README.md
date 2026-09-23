# PsionNet proxy

An HTTPS-terminating, HTML-3.2-downgrading HTTP proxy for the Psion Series 7.

## What it does

The Psion asks for `http://host/path` in cleartext. The proxy fetches
`https://host/path` itself with a modern TLS stack, extracts the readable
content, rewrites it to the HTML subset the Series 7 ROM can parse, and hands
it back as plain HTTP.

**No certificate forgery is involved.** The Psion never attempts a TLS
handshake, which is just as well, since the string `https` appears *zero*
times in its entire 16 MB ROM. There is no https scheme handler to forge a
certificate for. Every `https://` reference in the output is rewritten to
`http://` so the Psion routes it back through the proxy.

## Run it

```sh
python3 proxy/run.py                 # binds 10.0.2.1:8080, the Mac's PPP address
python3 proxy/run.py --host 127.0.0.1  # test from this Mac
python3 proxy/run.py --no-images       # defer images to links instead of inlining
```

Then on the Psion: **Web → Tools → Proxy server settings** → pick the service,
Protocol `http`, tick *Use proxy server*, Proxy server `10.0.2.1`, Port `8080`.
All fields must be filled or Web rejects the settings as incomplete.

`http://psion/` is the proxy's own home page and search form.

## Tests

```sh
python3 proxy/tests/test_units.py    # transforms, offline
python3 proxy/tests/test_wire.py     # end-to-end, hits the real network
```

`test_wire.py` drives the proxy with the exact bytes a Series 7 sends
absolute-URI request line, `User-Agent: EPOC32-WTL/2.0 (VGA)`, empty
`Accept-Encoding`. It asserts the reply is HTTP/1.0, unchunked, uncompressed,
`charset=iso-8859-1`, free of C1 bytes, free of `https://`, and free of any tag
outside the ROM's element table.

## Why the output looks the way it does

The whitelists in `sanitize.py` are not style choices. They were read out of
the Series 7 ROM image: the STNC "HTML Type Converter" registers its element
table at offset 7,080,368 and its attribute table at 7,084,000. Anything absent
from those tables is unknown to the parser.

Consequences worth knowing:

| Constraint | Why |
|---|---|
| No `id`, `class`, `style` or `title` attributes | Not in the ROM's attribute table. Fragment links must be `<a name="x">`. |
| Only GIF and baseline JPEG | The ROM registers exactly two image decoders. `image/png` occurs zero times ROM-wide. |
| GIF written non-interlaced, JPEG non-progressive | Pillow interlaces GIFs by default; progressive JPEG reports "Not supported" on the device. |
| Bytes `0x80-0x9F` never emitted | CP1252 smart punctuation on the ROM, C1 controls under ISO-8859-1 in Opera. The two disagree, so avoid the range entirely. |
| `&trade;` never emitted | Its value in the ROM's entity table is `0x63`, so it renders as a stray lowercase `c`. The proxy writes `(tm)`. |
| No gzip downstream | The ROM has no deflate or gzip handling; it sends an empty `Accept-Encoding`. |
| No `<meta refresh>` | No handler in the ROM. Real 3xx redirects are parsed and used instead. |
| Werkzeug pinned to HTTP/1.0 | Otherwise a response without `Content-Length` goes out chunked, and the ROM renders the hex markers as page text. |
| `form` and `picture` unwrapped, not deleted | Deleting them removes every form-wrapped content region and every responsive image. |

## Fidelity modes

`config.FIDELITY`, or `--fidelity` on the command line:

- **`medium`** (default): the whole body, pruned and cleaned. No extraction.
- **`lite`**: the old behaviour, readability extraction only. Smallest pages.
- **`full`**: as medium, with nothing shed.

The link was measured live at **10,650 wire octets/s**, so 10 seconds is about
100 KB. Byte-stuffing is a non-issue (0.76% text-vs-binary difference) because
`asyncmap 0` is negotiated. The caps sit deliberately *below* the wire ceiling
at 32 KB target / 48 KB hard, because Psion-side **render** time is still
unmeasured. Run `http://psion/bench/` with a stopwatch before raising them.

## Measured, medium fidelity

| Page | Upstream | To Psion | Links kept | Wire |
|---|---|---|---|---|
| BBC News | 1,041,215 B | 31,290 B | 152 | 3.0 s |
| Guardian | 1,495,860 B | 48,489 B | 180 | 4.7 s |
| Ars Technica | 355,917 B | 29,889 B | 185 | 2.9 s |
| Wikipedia article | 99,862 B | 24,050 B | 209 | 2.3 s |
| Hacker News | 34,103 B | 18,610 B | 231 | 1.8 s |
| gov.uk | 85,520 B | 10,329 B | 85 | 1.0 s |

Mac-side processing is 0.2-0.9 s per page, never the bottleneck.

### Where the bytes went

Three transformations do nearly all the work, and none of them is a CSS engine:

1. **Hidden-markup pruning** (`prune.py`) deletes `hidden`, `aria-hidden` and
   inline `display:none` subtrees. BBC News −52.5%, gov.uk −31.8%. On BBC most
   of that is four near-identical 43 KB `[hidden]` copies of the same story
   block shipped for different breakpoints, so it is deduplication, not loss.
2. **Same-origin URL relativisation**. `href`s are 26-48% of output bytes.
   Lossless: Hacker News −22.3%, Wikipedia −15.7%.
3. **Div collapse to a fixed point**. Modest byte savings, but it roughly halves
   the element count the 132 MHz StrongARM must lay out.

A safety valve reverts pruning wholesale if it would leave under 400 characters
of text, and regions containing a `<form>` are never pruned, because search boxes live
in exactly the collapsed nav drawers this would otherwise remove.

### Deliberately NOT built

Measured and rejected, not skipped for effort:

- **A CSS cascade engine.** An indexed hide-only version was built and diffed:
  it destroyed 99 genuine phrases on the Guardian front page.
- **CSS→presentational mapping** (`<font color>`, `bgcolor=`). Measured at
  **+8,610 bytes on BBC**. It costs bytes rather than saving them.
- **External stylesheet fetching.** Median +0.3% of page value for 68-1,754 ms
  on the critical path.
- **Multi-column table layout.** Measured off a real Series 7 screenshot, full
  width is ~59 characters; two columns is 29. No multi-column prose layout is
  legible. A page-shell layout table is also a single `<tr>`, so nothing paints
  until the last byte arrives, turning a progressive page into a blank screen.
- **Spacer GIFs and bgcolor border sandwiches.** The signature 1996-99
  techniques, all specifically broken on ER5: empty cells lose their background.

## Dependencies

```sh
pip install -r requirements.txt
```

Flask, requests, BeautifulSoup and Pillow. `PsionNet.app` bundles all of them,
so the app itself needs nothing installed.

`httpx[http2]` is optional but recommended. Measured interleaved on one IP
against `lite.duckduckgo.com` with an identical User-Agent: `requests` was
blocked 3/3, `httpx(http2=True)` got through 3/3. It is the header set urllib3
emits, not a TLS fingerprint. Without httpx the DuckDuckGo search backend is
skipped; wiby and Marginalia still work.

## Known limits

- **Search order is DuckDuckGo → Marginalia → wiby.** wiby was first and never
  returns empty, which starved the two better backends. DuckDuckGo returns HTTP
  202 to both GET and POST from a blocked IP and is treated as a sticky
  10-minute block; Marginalia is rate-limited per minute and gets one backoff.
- **`google.com` is intercepted, not proxied.** Google Search has required
  JavaScript since 15 January 2025. Every UA, cookie and legacy flag returns
  ~91 KB of script with 180 characters of visible text and zero results. The
  substitute page says so plainly and offers working search. It deliberately
  does not reproduce Google's branding.
- **Images are inlined by default**; `--no-images` defers them to numbered
  links instead. Note the Psion has its own switch: Web → Tools → Display
  preferences → *Load images automatically*. If that is off, nothing the proxy
  sends will appear inline.
- **Forms are unwrapped, so POST-based sites will not work.** Search forms that
  use GET do.
- **Some sites are on a deny-list** (`config.DENY_HOSTS`) because they return
  403/202 to any proxy regardless of headers, or serve empty JS shells. The
  proxy fails fast rather than making you wait for a timeout.
- **Untested against real hardware.** Every invariant above is asserted in
  `test_wire.py` against the documented ROM behaviour, but no byte of this has
  yet reached an actual Series 7.
