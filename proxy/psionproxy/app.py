"""The proxy itself.

Werkzeug is pinned to HTTP/1.0 below and that pin is mandatory, not tidiness:
BaseWSGIServer.__init__ sets handler.protocol_version = "HTTP/1.1" whenever
threaded, and Flask.run defaults threaded=True. At HTTP/1.1 any response
lacking a Content-Length goes out chunked, without ever checking what the
client spoke. Assigning the attribute here puts it in vars() and that guard
skips. Belt and braces: every handler below returns concrete bytes, never a
generator, so Content-Length is always computed.
"""

import hashlib
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qs

from bs4 import BeautifulSoup
from flask import Flask, Response, g, request
from werkzeug.serving import WSGIRequestHandler

from . import config, pages
from .extract import extract
from .fetch import FetchError, decode_body, fetch
from .images import transcode
from .sanitize import render, sanitize, serialize
from .search import search
from . import profiles
from .shed import shed_to_budget
from .textmap import encode

app = Flask(__name__)

# Headers that must never be relayed to the Psion.
_DROP_HEADERS = {
    "content-encoding", "content-length", "transfer-encoding", "connection",
    "strict-transport-security", "content-security-policy", "alt-svc",
    "upgrade-insecure-requests", "vary", "link", "nel", "report-to",
    "permissions-policy", "cross-origin-opener-policy",
    "cross-origin-embedder-policy", "cross-origin-resource-policy",
    "public-key-pins", "expect-ct", "set-cookie",
}

_image_cache: dict[str, tuple[bytes, str]] = {}

# Every request the device makes, appended to a file so it can be inspected
# from outside the terminal the proxy runs in.
# NOT /tmp: that directory is mode 1777 and this filename would be
# predictable, so every URL and search query the user browses would be
# world-readable, and a symlink planted at the path would make us append to a
# file of someone else's choosing.
_LOG_DIR = Path.home() / "Library" / "Logs" / "PsionNet"
REQUEST_LOG = str(_LOG_DIR / "proxy-requests.log")
_PID = __import__("os").getpid()


def _log(line: str) -> None:
    import os
    import time
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        # O_NOFOLLOW refuses to open a symlink; 0600 keeps the browsing history
        # readable only by its owner.
        fd = os.open(REQUEST_LOG,
                     os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
                     0o600)
        with os.fdopen(fd, "a") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [pid {_PID}] {line}\n")
    except Exception:
        pass


def _log_request(status: int, ctype: str, nbytes: int) -> None:
    try:
        ua = request.headers.get("User-Agent", "-")[:44]
        elapsed = ""
        started = getattr(g, "_t0", None)
        if started is not None:
            import time
            elapsed = f" {time.time() - started:.1f}s"
        _log(f"DONE  {_raw_url()[:130]} -> {status} "
             f"{ctype.split(';')[0]} {nbytes}B{elapsed} ua={ua}")
    except Exception:
        pass


def _respond(body: bytes, ctype: str, status: int = 200,
             extra: dict | None = None) -> Response:
    r = Response(body, status=status)
    r.headers["Content-Type"] = ctype
    r.headers["Content-Length"] = str(len(body))
    # Werkzeug already emits Connection: close at HTTP/1.0. Setting it again
    # produces a duplicate header, which is exactly the sort of thing a 1999
    # parser mishandles.
    for k, v in (extra or {}).items():
        r.headers[k] = v
    _log_request(status, ctype, len(body))
    return r


def _client_profile():
    """Which device is asking. Cached per request."""
    prof = getattr(g, "_profile", None)
    if prof is None:
        prof = profiles.for_user_agent(request.headers.get("User-Agent", ""))
        g._profile = prof
    return prof


def _html_response(markup: str, status: int = 200) -> Response:
    prof = _client_profile()
    # EPOC needs the CP1252/ISO-8859-1 intersection; CE renders UTF-8, so
    # encode it as such rather than destroying every non-Latin-1 character.
    body = encode(markup) if prof.downgrade_text else markup.encode("utf-8", "replace")
    return _respond(body, f"text/html; charset={prof.charset}", status,
                    {"Cache-Control": "no-store"})


def _raw_url() -> str:
    """Recover the absolute URI from the request line.

    Never use request.url: Werkzeug unconditionally unquotes PATH_INFO, so
    /wiki/Caf%C3%A9 arrives already decoded and /a%2Fb/c collapses to /a/b/c.
    """
    env = request.environ
    raw = env.get("REQUEST_URI") or env.get("RAW_URI") or request.url
    return urlunsplit(urlsplit(raw)._replace(fragment=""))


@app.before_request
def _guard():
    import time
    g._t0 = time.time()
    _log(f"START {request.method} {_raw_url()[:130]}")
    if request.method == "CONNECT":
        return _respond(b"This proxy does not tunnel TLS.\n", "text/plain", 501)
    if request.method == "OPTIONS" and request.path == "*":
        return _respond(b"", "text/plain", 200,
                        {"Allow": "GET, POST, HEAD, OPTIONS"})
    return None


@app.route("/", defaults={"path": ""}, methods=["GET", "POST", "HEAD", "OPTIONS"])
@app.route("/<path:path>", methods=["GET", "POST", "HEAD", "OPTIONS"])
def proxy(path):
    raw = _raw_url()
    parts = urlsplit(raw)

    # Locally-served pages. 'http://psion/...' is our own namespace.
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or host in ("psion", "", "10.0.2.1", "localhost"):
        return _local(parts)

    if path.startswith("i/") or parts.path.startswith("/i/"):
        return _serve_image(parts)

    # google.com is intercepted, not proxied: it requires JavaScript and
    # returns no results in HTML to any client. See pages.google_substitute.
    if host in ("google.com", "encrypted.google.com") or host.startswith("www.google."):
        q = parse_qs(parts.query).get("q", [""])[0]
        if q:
            results, backend = search(q)
            return _html_response(pages.search_results(q, results, backend))
        return _html_response(pages.google_substitute())

    return _proxy_remote(raw, parts)


def _local(parts) -> Response:
    route = parts.path or "/"
    query = parse_qs(parts.query)
    if route.rstrip("/") in ("/search", ""):
        q = (query.get("q") or [""])[0]
        if route.rstrip("/") == "/search":
            results, backend = search(q)
            return _html_response(pages.search_results(q, results, backend))
    if route.startswith("/i/"):
        return _serve_image(parts)
    if route.rstrip("/") == "/probe":
        return _html_response(pages.capability_probe())
    if route.startswith("/icon"):
        return _serve_asset(route.rsplit("/", 1)[-1] or "psionnet_48.gif")
    if route.startswith("/bench"):
        bits = [b for b in route.split("/") if b][1:]
        try:
            size = max(1, min(int(bits[0]), 200)) if bits else 25
        except ValueError:
            size = 25
        shape = bits[1] if len(bits) > 1 and bits[1] in ("prose", "nested", "table") else "prose"
        return _html_response(pages.bench(size, shape))
    return _html_response(pages.home())


def _assets_dir() -> Path:
    """Where our own GIFs live. Frozen, data sits outside the code archive."""
    import sys
    here = Path(__file__).resolve().parent
    roots = [here]
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        roots = [exe.parent.parent / "Resources" / "proxy" / "psionproxy",
                 exe.parent.parent / "Frameworks" / "proxy" / "psionproxy",
                 here]
        mei = getattr(sys, "_MEIPASS", None)
        if mei:
            roots.insert(0, Path(mei) / "proxy" / "psionproxy")
    for r in roots:
        if (r / "assets").is_dir():
            return r / "assets"
    return here / "assets"


def _serve_asset(name: str) -> Response:
    """Serve one of our own images to the Psion.

    GIF only: the ROM registers decoders for GIF and baseline JPEG and nothing
    else, so the PNG the Mac app uses would produce a save dialog instead of a
    picture.
    """
    ok_ext = name.endswith((".gif", ".png", ".jpg"))
    safe = name if ok_ext and "/" not in name and ".." not in name else ""
    path = _assets_dir() / safe if safe else None
    if path is None or not path.exists():
        return _html_response(pages.error("Not found", "No such image."), 404)
    try:
        blob = path.read_bytes()
    except OSError:
        return _html_response(pages.error("Not found", "No such image."), 404)
    mime = ("image/png" if safe.endswith(".png")
            else "image/jpeg" if safe.endswith(".jpg") else "image/gif")
    return _respond(blob, mime, 200, {"Cache-Control": "max-age=86400"})


def _serve_image(parts) -> Response:
    key = parts.path.rsplit("/", 1)[-1].split(".")[0]
    hit = _image_cache.get(key)
    if not hit:
        return _html_response(pages.error("Image expired",
                                          "Reload the page it came from."), 404)
    blob, mime = hit
    return _respond(blob, mime, 200, {"Cache-Control": "max-age=600"})


def _proxy_remote(raw: str, parts) -> Response:
    try:
        final_url, ctype, body = fetch(
            raw,
            referer=request.headers.get("Referer", ""),
            accept_language=request.headers.get("Accept-Language", ""))
    except FetchError as exc:
        return _html_response(
            pages.error(exc.message, exc.detail, raw, exc.retry_insecure), 502)

    ctype_main = (ctype or "").split(";")[0].strip().lower()

    if ctype_main.startswith("image/"):
        result = transcode(body, final_url)
        if result is None:
            return _html_response(pages.error(
                "Image cannot be shown",
                "It could not be reduced to a size and format the Psion can decode."),
                415)
        blob, mime, _ = result
        return _respond(blob, mime, 200, {"Cache-Control": "max-age=600"})

    if ctype_main and not ctype_main.startswith(("text/", "application/xhtml")):
        return _html_response(pages.error(
            "Unsupported file type",
            f"The server sent {ctype_main}, which the Psion has no decoder for."),
            415)

    text = decode_body(body, ctype)

    if ctype_main == "text/plain":
        safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return _html_response(render(final_url[:60], f"<pre>{safe[:40000]}</pre>",
                                     profile=prof))

    prof = _client_profile()
    body_html, was_article = extract(text, final_url, profile=prof)
    body_html, images = _budget_images(body_html)
    truncated = False
    if len(body_html.encode("utf-8", "replace")) > prof.budget_html_hard:
        shed_soup = BeautifulSoup(body_html, "html.parser")
        truncated, _ = shed_to_budget(shed_soup, prof.budget_html_hard)
        body_html = serialize(shed_soup)

    title = _title_of(text) or final_url
    nav = (f'<p><a href="http://psion/">PsionNet</a> | '
           f'<font size="1">{"article" if was_article else "links"}</font></p><hr>')
    tail = pages.images_row(images)
    if truncated:
        tail += '<hr><p><font size="1">[Page truncated to fit the link.]</font></p>'
    return _html_response(render(title, nav + body_html + tail, profile=prof))


_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def _title_of(html: str) -> str:
    m = _TITLE.search(html[:20000])
    return re.sub(r"\s+", " ", m.group(1)).strip()[:80] if m else ""


def _budget_images(body_html: str) -> tuple[str, list]:
    """Keep as many images as the page's remaining byte budget allows.

    Images are separate HTTP requests, so they never counted against the HTML
    cap -- which is how BBC News reached 19 seconds. Rank by declared area so
    the content photographs survive and the 20 px icons are the ones dropped,
    then demote the overflow to numbered links rather than losing it.
    """
    if config.IMAGES_DEFAULT_ON:
        # Emit every image, exactly as the original _defer_images did. The
        # capped version replaced this and images stopped rendering: it kept
        # the largest images by width*height and deferred everything else,
        # which discards precisely the small logos and icons that the device
        # renders most reliably. Capping is a byte optimisation, and it is not
        # worth breaking the page for -- the browser's own "Load images
        # automatically" setting already gates the cost.
        return body_html, []

    allowed = 0

    soup = BeautifulSoup(body_html, "html.parser")
    imgs = soup.find_all("img")
    if not imgs:
        return body_html, []

    def area(tag):
        try:
            return int(tag.get("width", 0)) * int(tag.get("height", 0))
        except (TypeError, ValueError):
            return 0

    ranked = sorted(imgs, key=area, reverse=True)
    keep = set(id(t) for t in ranked[:allowed])

    deferred = []
    for img in imgs:
        src = img.get("src") or ""
        if not src:
            img.decompose()
            continue
        if id(img) in keep:
            continue
        deferred.append(src)
        alt = (img.get("alt") or "").strip()
        label = f'[image {len(deferred)}{": " + alt if alt else ""}]'
        img.replace_with(BeautifulSoup(
            f'<font size="1">{label}</font>', "html.parser"))
    return serialize(soup), deferred


def _ppp_is_up() -> bool:
    """Is the PPP-side address actually assigned yet?

    ppp0 only gets its addresses once the Psion completes IPCP, so binding
    10.0.2.1 fails whenever the link is down -- which is most of the time,
    since closing Web on the Psion drops it.
    """
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((config.BIND_HOST, 0))
        return True
    except OSError:
        return False
    finally:
        s.close()


def main() -> None:
    # MANDATORY -- see module docstring.
    WSGIRequestHandler.protocol_version = "HTTP/1.0"

    host = config.BIND_HOST
    if not _ppp_is_up():
        # ppp0 only gets its addresses once the Psion completes IPCP, so
        # binding 10.0.2.1 fails whenever the link is down -- which is most of
        # the time, since closing Web on the Psion drops it. Listen on
        # everything instead: 10.0.2.1 is then covered the moment it appears.
        # Loopback, NOT 0.0.0.0. This is an open forward proxy that strips TLS;
        # binding it to every interface would offer that service to anything on
        # the LAN. Once ppp0 exists, restart to bind the PPP address.
        print(f"note: {config.BIND_HOST} is not up yet (the Psion is not connected),")
        print("      so listening on 127.0.0.1 instead -- you can test from this Mac.")
        print("      Restart once the link is up to serve the Psion.")
        host = "127.0.0.1"

    print(f"PsionNet proxy listening on {host}:{config.BIND_PORT}")
    print("Set the Psion: Web > Tools > Proxy server settings >")
    print(f"  Protocol http, Proxy server {config.BIND_HOST}, Port {config.BIND_PORT}")
    app.run(host=host, port=config.BIND_PORT, threaded=True, debug=False)
