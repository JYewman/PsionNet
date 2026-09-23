"""End-to-end wire-format tests using the real ER5 request shape.

The Psion sends an absolute-URI request line with User-Agent
'EPOC32-WTL/2.0 (VGA)' and an EMPTY Accept-Encoding. Everything here asserts
what comes back is something its ROM can actually consume.
"""
import re
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from werkzeug.serving import make_server, WSGIRequestHandler
from psionproxy.app import app

PSION_UA = "EPOC32-WTL/2.0 (VGA) STNC-WTL/2.0(14)"
ALLOWED_TAGS = {
    "a", "address", "b", "base", "basefont", "big", "blockquote", "body", "br",
    "caption", "center", "cite", "code", "dd", "dfn", "dir", "div", "dl", "dt",
    "em", "font", "form", "h1", "h2", "h3", "h4", "h5", "h6", "head", "hr",
    "html", "i", "img", "input", "isindex", "kbd", "li", "map", "area", "menu",
    "meta", "ol", "option", "p", "pre", "samp", "select", "small", "strike",
    "strong", "sub", "sup", "table", "td", "textarea", "th", "title", "tr",
    "tt", "u", "ul", "var", "!doctype",
}


def start():
    WSGIRequestHandler.protocol_version = "HTTP/1.0"
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.4)
    return srv, srv.server_address[1]


def psion_get(port, url, timeout=45):
    """Speak exactly what the ROM speaks."""
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    s.sendall(
        f"GET {url} HTTP/1.0\r\n"
        f"User-Agent: {PSION_UA}\r\n"
        f"Accept: text/html, image/gif, image/jpeg, text/plain, */*\r\n"
        f"Accept-Encoding: \r\n"
        f"Connection: close\r\n\r\n".encode("ascii")
    )
    chunks = []
    while True:
        try:
            b = s.recv(65536)
        except socket.timeout:
            break
        if not b:
            break
        chunks.append(b)
    s.close()
    raw = b"".join(chunks)
    head, _, body = raw.partition(b"\r\n\r\n")
    return head.decode("latin-1"), body


def check(name, head, body, expect_html=True):
    fails = []
    status = head.splitlines()[0]
    lower = head.lower()

    if not status.startswith("HTTP/1.0"):
        fails.append(f"response is {status.split()[0]}, not HTTP/1.0")
    if "transfer-encoding: chunked" in lower:
        fails.append("chunked transfer-encoding (ROM would render hex markers)")
    if "content-length:" not in lower:
        fails.append("no Content-Length")
    if "content-encoding:" in lower:
        fails.append("compressed body (ROM has no gzip/deflate)")

    c1 = sorted({b for b in body if 0x80 <= b < 0xA0})
    if c1:
        fails.append(f"C1 bytes present: {[hex(b) for b in c1][:6]}")

    if expect_html:
        if "charset=iso-8859-1" not in lower:
            fails.append("Content-Type is not iso-8859-1")
        text = body.decode("latin-1")
        bad_attr = re.findall(r'(?:href|src|action)\s*=\s*"(?:https://|//)', text)
        if bad_attr:
            fails.append(f"https:// or protocol-relative URL in {len(bad_attr)} attribute(s)")
        for bad in ("&mdash;", "&rsquo;", "&ldquo;", "&hellip;", "&bull;",
                    "&trade;", "&euro;", "&#"):
            if bad in text:
                fails.append(f"unsupported entity {bad}")
        tags = {t.lower() for t in re.findall(r"<\s*/?\s*([a-zA-Z!][a-zA-Z0-9]*)", text)}
        stray = tags - ALLOWED_TAGS
        if stray:
            fails.append(f"tags outside HTML 3.2 whitelist: {sorted(stray)[:8]}")
        if "<script" in text.lower() or "<style" in text.lower():
            fails.append("script/style leaked")
        if " class=" in text or " id=" in text or " style=" in text:
            fails.append("class/id/style attribute leaked")

    status_word = "PASS" if not fails else "FAIL"
    print(f"[{status_word}] {name}  ({len(body):,} B)")
    for f in fails:
        print(f"         - {f}")
    return not fails


if __name__ == "__main__":
    srv, port = start()
    ok = True
    ok &= check("home page", *psion_get(port, "http://psion/"))
    ok &= check("example.com (https upstream)", *psion_get(port, "http://example.com/"))
    ok &= check("Wikipedia article", *psion_get(port, "http://en.wikipedia.org/wiki/Psion_Series_7"))
    ok &= check("Hacker News (index mode)", *psion_get(port, "http://news.ycombinator.com/"))
    ok &= check("BBC News (heavy page)", *psion_get(port, "http://www.bbc.co.uk/news"))
    ok &= check("denylisted host", *psion_get(port, "http://nytimes.com/"))
    ok &= check("search", *psion_get(port, "http://psion/search?q=psion+series+7"))
    srv.shutdown()
    print("\nALL PASS" if ok else "\nFAILURES ABOVE")
    sys.exit(0 if ok else 1)
