"""Proxy-generated pages: home, search results, errors.

All emitted through the same HTML 3.2 renderer as proxied content, so the
invariants (no https, no C1 bytes, HTML 3.2 only) hold here too.
"""

from urllib.parse import quote_plus

from .sanitize import _http_only, render
from .textmap import psionise


def plain(url: str) -> str:
    """Force http:// on any link we emit.

    The Psion has no https scheme handler at all, so an https:// href is a
    dead end -- it never reaches us and never gets upgraded. The proxy does
    the TLS upstream; the markup must always say http.
    """
    return _http_only(url or "")

# Measured decompressed weights, and seconds at ~11 KB/s over PPP.
LINKS = [
    ("http://frogfind.com/", "FrogFind", "1 KB - search, built for vintage browsers"),
    ("http://wiby.me/surprise/", "wiby surprise", "<1 KB - a random small-web page"),
    ("http://text.npr.org/", "NPR text", "6 KB - news, text only"),
    ("http://theoldnet.com/", "TheOldNet", "77 KB - 1990s web, native ISO-8859-1"),
    ("http://news.ycombinator.com/", "Hacker News", "34 KB"),
    ("http://68k.news/", "68k.news", "99 KB - headlines for retro machines"),
    ("http://lobste.rs/", "Lobsters", "60 KB"),
]


def _nav() -> str:
    return ('<p><img src="http://psion/icon/psionnet_32.gif" width="32" height="32" '
            'alt="" align="middle"> '
            '<a href="http://psion/">Home</a> | '
            '<a href="http://psion/search">Search</a></p><hr>')


def home() -> str:
    rows = "\n".join(
        f'<li><a href="{plain(url)}">{psionise(name)}</a> <font size="1">{psionise(note)}</font></li>'
        for url, name, note in LINKS)
    body = (
        # 1.4 KB GIF, ~140 ms of link time. GIF because the ROM has no PNG
        # decoder at all.
        '<p><img src="http://psion/icon/psionnet_48.gif" width="48" height="48" '
        'alt="PsionNet"></p>'
        "<h2>PsionNet</h2>"
        '<form action="http://psion/search" method="get">'
        '<p>Search: <input type="text" name="q" size="28">'
        ' <input type="submit" value="Go"></p></form>'
        "<hr><h3>Light pages that load quickly</h3>"
        f"<ul>{rows}</ul>"
        '<hr><p><font size="1"><a href="http://psion/bench/25/prose">'
        "Render benchmark</a> - times this Psion's own layout engine.</font></p>"
        "<p><font size=\"1\">Type any address into Web as normal. "
        "The proxy fetches it over a secure connection and hands it back as "
        "plain HTML 3.2. Images are off by default.</font></p>")
    return render("PsionNet", body)


def search_results(query: str, results: list, backend: str) -> str:
    if not results:
        body = (_nav() + f"<h3>No results for {psionise(query)}</h3>"
                "<p>Every search backend either returned nothing or is rate-limiting. "
                f'You can try <a href="http://frogfind.com/?q={quote_plus(query)}">'
                "FrogFind</a> directly.</p>")
        return render(f"No results: {query}", body)

    rows = []
    for title, url, snippet in results:
        snip = psionise(snippet)[:220]
        rows.append(
            f'<p><a href="{plain(url)}">{psionise(title)[:110]}</a><br>'
            f'<font size="1">{snip}</font></p>')
    body = (_nav() + f"<h3>{psionise(query)}</h3>" + "\n".join(rows) +
            f'<hr><p><font size="1">via {psionise(backend)}</font></p>')
    return render(f"Search: {query}", body)


def error(title: str, detail: str, url: str = "", retry_insecure: bool = False) -> str:
    extra = ""
    if retry_insecure and url:
        sep = "&" if "?" in url else "?"
        extra = f'<p><a href="{plain(url)}{sep}psx_insecure=1">Try anyway, without verifying</a></p>'
    body = (_nav() + f"<h3>{psionise(title)}</h3><p>{psionise(detail)}</p>" + extra +
            (f'<p><font size="1">{psionise(url)[:300]}</font></p>' if url else ""))
    return render(title, body)


def images_row(items: list) -> str:
    """Numbered links for images that did not fit the page budget.

    Capped, because the row is not free: the Guardian front page carries 91
    images whose URLs alone came to 22 KB -- half the page budget spent on
    links to pictures nobody asked for.
    """
    if not items:
        return ""
    from . import config
    shown = items[:config.BUDGET_DEFERRED_LINKS]
    links = " ".join(
        '<a href="%s">[%d]</a>' % (plain(src), i)
        for i, src in enumerate(shown, 1))
    more = ""
    if len(items) > len(shown):
        more = " (%d more not listed)" % (len(items) - len(shown))
    return '<hr><p><font size="1">Images: %s%s</font></p>' % (links, more)


def bench(size_kb: int, shape: str = "prose") -> str:
    """A fixed-size synthetic page, for timing the Psion's own renderer.

    Wire time is calculable; RENDER time on a 132 MHz StrongARM is the one
    term in the latency model with no measurement behind it. Load these with
    a stopwatch and compare against the stated wire time to find out whether
    parsing or the serial link is the real ceiling.
    """
    target = size_kb * 1024
    if shape == "nested":
        depth = 500
        inner = "Nested wrapper test. " * 40
        body = "<div>" * depth + inner + "</div>" * depth
        while len(body) < target:
            body += "<div><div><div>" + inner + "</div></div></div>"
    elif shape == "table":
        rows = []
        i = 0
        while sum(len(r) for r in rows) < target:
            i += 1
            rows.append(f"<tr><td>{i}</td><td>Row {i} column two</td>"
                        f"<td>Some cell text for row {i}</td><td>{i * 7}</td></tr>")
        body = ('<table border="1" cellpadding="2"><tr><th>#</th><th>Name</th>'
                '<th>Text</th><th>Value</th></tr>' + "".join(rows) + "</table>")
    else:
        para = ("<p>This is a paragraph of ordinary prose used to time the "
                "Series 7 renderer. It contains no images, no tables and no "
                "nesting, so it measures raw text layout speed alone.</p>\n")
        body = para * max(1, target // len(para))

    wire = len(body) / 10335.0
    header = (f'<h3>Bench: {size_kb} KB, {psionise(shape)}</h3>'
              f'<p>Expected wire time at 10.3 KB/s: <b>{wire:.1f} s</b>.<br>'
              f'Time from tapping the link to the page settling, then subtract '
              f'the wire time. The remainder is render time.</p>'
              f'<p>Other sizes: '
              + " ".join(f'<a href="http://psion/bench/{n}/{shape}">{n}K</a>'
                         for n in (10, 25, 50, 100))
              + '<br>Other shapes: '
              + " ".join(f'<a href="http://psion/bench/{size_kb}/{s}">{s}</a>'
                         for s in ("prose", "nested", "table"))
              + '</p><hr>')
    return render(f"Bench {size_kb}K {shape}", _nav() + header + body)


def google_substitute(query: str = "") -> str:
    """Served in place of google.com, which cannot work on this device.

    Google Search has required JavaScript since 15 January 2025. Measured with
    every User-Agent, consent cookie and legacy flag: ~91 KB of script and 180
    characters of visible text. There is no proxy trick that recovers results.

    Deliberately NOT dressed up as Google -- no logo, no imitation. Showing
    Google's branding on a page that is not Google would misrepresent it.
    """
    body = (_nav() +
            "<h3>Google cannot work on the Series 7</h3>"
            "<p>Google Search has required JavaScript since January 2025. "
            "It now sends about 91 KB of script and 180 characters of text, "
            "with no results in the HTML at all, so there is nothing for this "
            "browser to display. This is true for every address, cookie and "
            "legacy flag, including the old <tt>gbv=1</tt> basic-HTML mode.</p>"
            "<p>PsionNet search works instead, and uses DuckDuckGo, Marginalia "
            "and wiby:</p>"
            '<form action="http://psion/search" method="get">'
            '<p>Search: <input type="text" name="q" size="28" value="'
            + psionise(query) + '"> <input type="submit" value="Go"></p></form>'
            '<hr><p><font size="1">Other search that renders here: '
            '<a href="http://frogfind.com/">FrogFind</a>, '
            '<a href="http://wiby.me/">wiby</a>, '
            '<a href="http://www.mojeek.com/">Mojeek</a>.</font></p>')
    return render("Google", body)
