# PsionNet proxy

An HTTPS-terminating, downgrading HTTP proxy for Psion hardware. It serves
three quite different browsers and shapes its output to whichever one is
asking. For the netBook Pro running PsionLX it also carries a Spotify bridge;
see [Spotify](#spotify).

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

### Network mode

For a netBook Pro on the LAN rather than the serial cable:

```sh
python3 proxy/run.py --host 192.168.1.4 --allow 192.168.1.0/24
python3 proxy/run.py --host 192.168.1.4 --allow 192.168.1.0/24 --spotify
python3 proxy/run.py --host 127.0.0.1 --allow 127.0.0.1/32 --spotify-demo
```

`--host` must be one of this Mac's own addresses; the proxy exits if it is not.
`--allow` takes comma-separated networks. With it, the proxy serves loopback
and those networks only, refusing everyone else with a 403, and answers
discovery broadcasts (`PSIONNET?` on UDP 8899) from the same networks with
`PSIONNET <host> <port>`. Without `--allow`, as on the serial link, nothing is
filtered, because only the device on the end of the cable can reach the PPP
address.

`--spotify` starts the Spotify bridge. `--spotify-demo` starts a stand-in with
five canned tracks and generated tones, so the whole path can be tested with
no Spotify account and no librespot.

`--software` serves PsionLX's software library (`software.py`):

| Request | Reply |
|---|---|
| `/lx/software/<file>` | that file of `https://archive.retrotechcollection.com/PsionLX-Software`, byte for byte |
| `/lx/install` | its `install.sh`, with the address the device used to reach PsionNet filled in |

The proxy's usual rewriting of pages and images would ruin a package, so this
is a separate route, and it serves that one folder: names are checked, `..`
and hidden files are refused, and nothing above the folder is reachable. The
lists are cached for a minute. `--software-src` points it at another URL, or
at a local folder to test a feed before it is uploaded.

## Tests

```sh
python3 proxy/tests/test_units.py    # transforms, offline
python3 proxy/tests/test_wire.py     # end-to-end, hits the real network
python3 proxy/tests/test_network.py  # network mode, discovery, Spotify (demo)
```

`test_wire.py` drives the proxy with the exact bytes a Series 7 sends
absolute-URI request line, `User-Agent: EPOC32-WTL/2.0 (VGA)`, empty
`Accept-Encoding`. It asserts the reply is HTTP/1.0, unchunked, uncompressed,
`charset=iso-8859-1`, free of C1 bytes, free of `https://`, and free of any tag
outside the ROM's element table.

## Client profiles

The proxy picks a profile from the User-Agent. There is no setting to change
and nothing to configure on the device.

| | Series 7 / 5mx / netBook / Revo | netBook Pro | netBook Pro, PsionLX |
|---|---|---|---|
| OS | EPOC Release 5 | Windows CE 4.2 .NET | Psion's Linux, 2005 |
| Browser | STNC WTL 2.0, in ROM | Pocket Internet Explorer | Firefox 1.0 (Gecko 1.7) |
| User-Agent | `EPOC32-WTL/2.0 (VGA)` | `Mozilla/4.0 (compatible; MSIE 6.0; Windows CE)` | `... rv:1.7.x) Gecko/... Firefox/1.0` |
| Output | HTML 3.2 | HTML 4.01 Transitional | HTML 4.01 Transitional |
| Charset | ISO-8859-1, flattened | UTF-8, untouched | UTF-8, untouched |
| CSS | stripped entirely | filtered to the IE6 subset | filtered to the IE6 subset |
| `class` / `id` | stripped | kept where a retained rule uses them | as CE |
| Images | non-interlaced GIF, baseline JPEG | same, PNG fails on both | GIF and JPEG, as the others |
| Link speed | 115200, about 11 KB/s | 19200, about 1.9 KB/s | the LAN |
| Byte budget | 64 KB | 192 KB | 400 KB, CSS 96 KB |

PsionLX's Firefox 1.0 speaks TLS 1.0 at best, which no modern site accepts,
so it needs the proxy as much as the others do. Gecko 1.7 renders more CSS
than Pocket IE, but not what modern pages are built from (flex, grid,
`calc()`, `rem`, custom properties), so it gets the same filter. Its budgets
are set by what a 400 MHz XScale lays out comfortably, not by wire time. The
User-Agent match is deliberately narrow: `Firefox/1.x`, or Gecko `rv:1.0` to
`rv:1.8`, never a modern `Firefox/128.0`.

An unknown User-Agent gets the EPOC profile. Strict output is valid input for
a richer browser, so guessing that way costs fidelity; guessing the other way
produces a page the device cannot render at all.

### How the CE profile was derived

The EPOC target was read out of the ROM. The CE target could not be, so a
capability probe was written instead, served to a real netBook Pro at
`http://psion/probe`, and the results recorded:

| Test | Result | Consequence |
|---|---|---|
| GIF, baseline JPEG | rendered | keep transcoding |
| PNG | **did not render** | still no PNG passthrough |
| HTML entities | rendered | |
| Raw UTF-8 bytes | rendered | no character flattening |
| CSS `style=` attribute | rendered | inline styles kept |
| CSS `<style>` block | rendered | style blocks kept |

### Why the CSS is filtered rather than forwarded

Pocket IE renders CSS, but it is an IE6-era engine and modern inline CSS is
overwhelmingly things it cannot use. Measured on the live pages:

| | Inline CSS | At 19200 | Feature queries | rem units | flex/grid | calc() |
|---|---|---|---|---|---|---|
| BBC News | 190,162 B | 100 s | 487 | 1,056 | 160 | 90 |
| The Guardian | 844,338 B | 441 s | many | many | many | many |
| Wikipedia | 10,182 B | 5 s | 5 | 0 | 0 | 0 |

Forwarding BBC's stylesheet whole would spend a hundred seconds delivering
rules the device discards on parse. `css.py` keeps only the subset IE6
implements, which is also roughly the subset designed for screens this size:
colours, fonts, borders, alignment, spacing.

| | Before | After | Kept |
|---|---|---|---|
| BBC News | 190,162 B | 32,449 B | 17% |
| The Guardian | 844,338 B | 41,925 B | 5% |
| Wikipedia | 10,182 B | 5,071 B | 49% |

Two things it deliberately refuses even though IE6 understands them:

- **Reset rules.** `html,body,div,span,...{margin:0;padding:0}` assumes the
  design rules that follow will put the spacing back, and most of those have
  just been dropped as un-renderable. Keeping the reset alone leaves headings
  and paragraphs run together, visibly worse than no stylesheet. Both BBC News
  and the Guardian open with one.
- **Declarations that strip default semantics** on bare element selectors:
  `ol,ul{list-style:none}`, `a{text-decoration:none}`,
  `h1{font-weight:normal}`. On this screen a bullet and an underline are how
  you tell what something is. A scoped rule like `.nav ul{list-style:none}` is
  left alone, because the author meant that one.

### Dead class attributes

Keeping `class` is worth its bytes exactly when some retained rule selects on
it. After the filter runs, far fewer qualify than the page shipped. Measured
before this pass:

| | `class=` bytes | Share of page | Names referenced |
|---|---|---|---|
| BBC News | 66,369 B | 50% | 0 of 319 |
| Wikipedia | 8,898 B | 16% | 17 of 159 |
| Hacker News | 4,144 B | 16% | 0 of 11 |

Those bytes are not free. The page is shed to the byte budget afterwards, so
dead attribute strings were displacing real content on the slower of the two
links. An `id` survives if a rule selects it or a same-page fragment link
targets it.

`<link>` tags are dropped outright. A Wikipedia article carries eight and none
is a stylesheet: seven are category metadata and one a TemplateStyles marker,
about 640 B that paints nothing. External stylesheets are dropped too, since a
blocking fetch of tens of KB is not affordable at 1.9 KB/s.

### Measured output, both profiles

| Site | Upstream | EPOC | at 11 KB/s | CE | at 1.9 KB/s |
|---|---|---|---|---|---|
| BBC News | 944 KB | 39.6 KB | 3.6 s | 135.6 KB | 71 s |
| The Guardian | 1,711 KB | 112.2 KB | 10.2 s | 192 KB (capped) | 101 s |
| Hacker News | 35 KB | 18.9 KB | 1.7 s | 20.3 KB | 11 s |
| gov.uk | 86 KB | 10.0 KB | 0.9 s | 10.9 KB | 6 s |

The CE figures are larger and slower on purpose. That device can show the
formatting, so it is sent.

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

## Spotify

`psionproxy/spotify/` is the Mac half of Spotify on the netBook Pro running
PsionLX. The netBook Pro half is a GTK 2.4 program in the PsionLX image,
`psionnet-spotify`.

```
 Spotify  <--  librespot (Connect speaker "netBook Pro")  -- raw PCM -->  Pump
                                                                           |
 netBook Pro  <--  HTTP /spotify/stream.mp3  <--  Broadcaster  <--  MP3 128k
      |
      +-- HTTP /spotify/status, /playlists, /play ...  -->  Web API (PKCE)
```

- `webapi.py`: Spotify's Web API with the user's own Client ID and a PKCE
  login (no client secret). Tokens refresh themselves and are stored 0600.
- `librespot.py`: runs `librespot --backend pipe --format S16` from cached
  credentials. They come from librespot's own browser sign-in, run once as a
  separate process: Spotify refuses a speaker signed in with a third-party
  Client ID's token ("could not initialize spirc: Login request was denied:
  INVALID_CREDENTIALS"), and librespot prints its sign-in link on stdout,
  which in the speaker is the audio. Refused credentials are dropped and the
  sign-in runs again, once per start, never in a loop. Restarted with backoff
  if it dies.
- `audio.py`: librespot writes PCM as fast as the pipe takes it and counts its
  playback position from what it has written, so the pump reads at exactly
  real time, and fills gaps with silence so the device's GStreamer 0.8 is
  never starved. LAME (via lameenc) encodes 128 kbit/s MP3, comfortable for
  libmad on a 400 MHz XScale. The stream is cut into whole MP3 frames, so a
  listener joining, or losing chunks by falling behind, never starts
  mid-frame.
- `routes.py`: the app's API, plain text, one record per line, fields
  separated by TABs: easy to parse in C on a 2005 userspace.

| Request | Reply |
|---|---|
| `/spotify/hello` | `state` (ready, login, starting, error), `message` |
| `/spotify/status` | `playing`, `here`, `device`, `title`, `artist`, `album`, `art`, `progress`, `duration`, `volume` ... |
| `/spotify/playlists` | Liked Songs first, then the user's playlists |
| `/spotify/tracks?uri=` | the tracks of a playlist, an album, or `spotify:liked` |
| `/spotify/search?q=&type=track\|album\|playlist` | matching rows |
| `/spotify/play?uri=&context=` | play a track within its playlist or album |
| `/spotify/pause`, `resume`, `next`, `previous`, `volume?v=0..100` | `OK` |
| `/spotify/stream.mp3` | the audio, never-ending |
| `/spotify/art?u=&s=64` | a cover as a small baseline JPEG, Spotify's image hosts only |

Every reply starts `OK`, or `ERR<TAB>message` that the app shows as it is.
Requests other than `hello`, the stream and the art need an
`X-PsionNet-Client` header, which the app sends and a web page in the
device's browser cannot.

## Dependencies

```sh
pip install -r requirements.txt
```

Flask, requests, BeautifulSoup and Pillow; lameenc for Spotify, plus
`brew install librespot`. `PsionNet.app` bundles all of them, so the app
itself needs nothing installed.

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
- **Google cannot work on either device**, for two different reasons. EPOC and
  any modern User-Agent get a JavaScript-only results page: about 91 KB of
  script and 102 characters of visible text. Windows CE is refused by name
  before any script runs, with 2,318 bytes reading "Your browser isn't
  supported any more". Accepting the cookie notice reaches the same block, so
  consent is not the gate. `google.com` is intercepted and a working search
  page served instead.
