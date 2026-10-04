"""Upstream client. This is the half that speaks modern TLS.

No certificate forgery is involved: the Psion asks us for http://host/path in
cleartext, we fetch https://host/path ourselves with a current TLS stack, and
we hand back plaintext with every https:// reference rewritten. The Psion never
attempts a TLS handshake, which is just as well -- 'https' appears zero times
in its entire ROM.

httpx with HTTP/2 is strongly preferred over requests. Measured interleaved on
one IP against lite.duckduckgo.com with an identical User-Agent: requests was
blocked 3/3, httpx(http2=True) succeeded 3/3. It is not a TLS fingerprint
difference -- both use the same OpenSSL -- it is the header set urllib3 emits.
"""

import re
from urllib.parse import urlsplit, urlunsplit

from . import config
from . import host as machine

try:                      # preferred path
    import httpx
    _HTTPX = True
except ImportError:       # still works, just gets blocked more often
    _HTTPX = False
import requests

_META_CHARSET = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_\-]+)""", re.I)

_scheme_cache: dict[str, str] = {}


class FetchError(Exception):
    """Carries a message already fit for rendering to the Psion."""

    def __init__(self, message: str, detail: str = "", retry_insecure: bool = False):
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.retry_insecure = retry_insecure


def _client():
    if _HTTPX:
        try:
            return httpx.Client(http2=True, follow_redirects=True,
                                timeout=httpx.Timeout(config.UPSTREAM_TIMEOUT[1],
                                                      connect=config.UPSTREAM_TIMEOUT[0]))
        except ImportError:
            # httpx installed without the h2 extra
            return httpx.Client(follow_redirects=True,
                                timeout=httpx.Timeout(config.UPSTREAM_TIMEOUT[1],
                                                      connect=config.UPSTREAM_TIMEOUT[0]))
    s = requests.Session()
    return s


def _ua_for(host: str) -> str:
    if host.endswith("wikipedia.org") or host.endswith("wikimedia.org"):
        return config.UPSTREAM_UA_WIKIMEDIA
    return config.UPSTREAM_UA


def _candidates(url: str) -> list[str]:
    """https first, plaintext second -- unless the host is known to need http."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.netloc.endswith(":443"):
        parts = parts._replace(netloc=parts.netloc[:-4])
    bare = urlunsplit(parts._replace(scheme="", fragment=""))[2:]
    if host in config.HTTP_ONLY_HOSTS:
        return [f"http://{bare}"]
    remembered = _scheme_cache.get(host)
    if remembered == "http":
        return [f"http://{bare}", f"https://{bare}"]
    return [f"https://{bare}", f"http://{bare}"]


def decode_body(raw: bytes, content_type: str) -> str:
    """Decode with the declared charset, then the meta tag, then utf-8.

    Never use requests' .text here: for text/html with no declared charset it
    falls back to ISO-8859-1 and mojibakes anything UTF-8, bbc.co.uk included.
    """
    charset = ""
    if "charset=" in (content_type or "").lower():
        charset = content_type.lower().split("charset=", 1)[1].split(";")[0].strip(' "\'')
    if not charset:
        m = _META_CHARSET.search(raw[:8192])
        if m:
            charset = m.group(1).decode("ascii", "ignore")
    for enc in (charset, "utf-8", "cp1252", "latin-1"):
        if not enc:
            continue
        try:
            return raw.decode(enc, errors="replace")
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("latin-1", errors="replace")


def _is_internal(host: str) -> bool:
    """Would fetching this reach the host's own machine or private network?

    Without this the proxy is a server-side request forgery pivot: anything
    that can reach it could ask for http://127.0.0.1:<port>/ or an address on
    the operator's LAN and read the response back.
    """
    import ipaddress
    import socket
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
            return True
    return False


def fetch(url: str, referer: str = "", accept_language: str = ""):
    """Return (final_url, content_type, body_bytes). Raises FetchError."""
    host = (urlsplit(url).hostname or "").lower()
    if host and _is_internal(host):
        raise FetchError(
            "Address not allowed",
            "That address is on a private or local network. The proxy only "
            "fetches from the public internet.")
    root = host[4:] if host.startswith("www.") else host
    if root in config.DENY_HOSTS or host in config.DENY_HOSTS:
        raise FetchError(
            "This site blocks proxies",
            f"{host} refuses automated requests regardless of how we ask, "
            "so the proxy stops here rather than making you wait for a timeout.")

    headers = {"User-Agent": _ua_for(host),
               "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
    if referer:
        headers["Referer"] = referer
    if accept_language:
        headers["Accept-Language"] = accept_language

    last: Exception | None = None
    ssl_failed = False
    for candidate in _candidates(url):
        try:
            client = _client()
            try:
                if _HTTPX and isinstance(client, httpx.Client):
                    with client.stream("GET", candidate, headers=headers) as r:
                        body = _read_capped_httpx(r)
                        ctype = r.headers.get("content-type", "")
                        final = str(r.url)
                        status = r.status_code
                else:
                    r = client.get(candidate, headers=headers, stream=True,
                                   timeout=config.UPSTREAM_TIMEOUT, allow_redirects=True)
                    body = _read_capped_requests(r)
                    ctype = r.headers.get("content-type", "")
                    final = r.url
                    status = r.status_code
            finally:
                try:
                    client.close()
                except Exception:
                    pass

            if status >= 400:
                raise FetchError(f"Server returned {status}",
                                 f"{candidate} responded with HTTP {status}.")
            _scheme_cache[host] = urlsplit(final).scheme
            return final, ctype, body

        except FetchError:
            raise
        except Exception as exc:                      # noqa: BLE001
            # SSLError subclasses ConnectionError in requests, so test it first.
            name = type(exc).__name__
            if "SSL" in name or "Certificate" in name:
                ssl_failed = True
            last = exc
            continue

    if ssl_failed:
        raise FetchError(
            "Secure connection failed",
            f"The site's certificate could not be verified from {machine.THIS}. "
            f"That is a problem between {machine.THIS} and the site, not the Psion.",
            retry_insecure=True)
    raise FetchError("Could not reach the site", str(last or "unknown error"))


def _read_capped_httpx(r) -> bytes:
    chunks, total = [], 0
    for chunk in r.iter_bytes():        # already decompressed
        chunks.append(chunk)
        total += len(chunk)
        if total > config.BUDGET_UPSTREAM_MAX:
            break
    return b"".join(chunks)


def _read_capped_requests(r) -> bytes:
    chunks, total = [], 0
    for chunk in r.iter_content(65536):
        chunks.append(chunk)
        total += len(chunk)
        if total > config.BUDGET_UPSTREAM_MAX:
            break
    return b"".join(chunks)
