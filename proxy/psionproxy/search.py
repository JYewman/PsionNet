"""Search, re-rendered as our own minimal markup.

Never pass a search engine's HTML through to the Psion -- it is all far too
heavy. Query a backend, take the structured results, emit our own page.

Backends are tried in order of how reliably they answer an automated request:
wiby is a flat JSON API that has never refused us; Marginalia rate-limits per
minute but recovers; DuckDuckGo blocks aggressively and only works over HTTP/2.
"""

import html as _html
import json
import time
from urllib.parse import quote_plus, urlsplit, parse_qs, unquote

from . import config
from .fetch import _client, _HTTPX

_cache: dict[str, tuple[float, list]] = {}
_last_ddg = 0.0


def _cached(query: str):
    hit = _cache.get(query.lower().strip())
    if hit and time.time() - hit[0] < config.SEARCH_CACHE_SECONDS:
        return hit[1]
    return None


def _store(query: str, results: list) -> None:
    _cache[query.lower().strip()] = (time.time(), results)


def _get(url: str, headers: dict | None = None, timeout: int = 12):
    client = _client()
    try:
        hdrs = {"User-Agent": config.UPSTREAM_UA}
        hdrs.update(headers or {})
        if _HTTPX:
            r = client.get(url, headers=hdrs)
            return r.status_code, r.text
        r = client.get(url, headers=hdrs, timeout=timeout)
        return r.status_code, r.text
    finally:
        try:
            client.close()
        except Exception:
            pass


def _post(url: str, data: dict, timeout: int = 12):
    client = _client()
    try:
        hdrs = {"User-Agent": config.UPSTREAM_UA,
                "Content-Type": "application/x-www-form-urlencoded"}
        if _HTTPX:
            r = client.post(url, headers=hdrs, data=data)
        else:
            r = client.post(url, headers=hdrs, data=data, timeout=timeout)
        return r.status_code, r.text
    except Exception:
        return 0, ""
    finally:
        try:
            client.close()
        except Exception:
            pass


def _wiby(query: str) -> list:
    status, text = _get(f"https://wiby.me/json/?q={quote_plus(query)}")
    if status != 200:
        return []
    try:
        rows = json.loads(text)
    except ValueError:
        return []
    out = []
    for row in rows[:12]:
        if not isinstance(row, dict):
            continue
        url = _html.unescape(row.get("URL", "") or "")
        title = _html.unescape(row.get("Title", "") or "") or url
        snippet = _html.unescape(row.get("Snippet", "") or row.get("Description", "") or "")
        if url:
            out.append((title, url, snippet))
    return out


def _marginalia(query: str) -> list:
    """Marginalia gives by far the best results for this kind of query, but
    the public key is rate-limited PER MINUTE -- it returns 429 on rapid
    repeats and recovers on its own. One short backoff is worth it; a single
    person on a serial link is nowhere near the sustained limit."""
    url = (f"https://api2.marginalia-search.com/search?"
           f"query={quote_plus(query)}&count=10")
    for attempt in range(2):
        status, text = _get(url, headers={"API-Key": "public"})
        if status == 200:
            break
        if status == 429 and attempt == 0:
            time.sleep(2.5)
            continue
        return []
    if status != 200:
        return []
    try:
        data = json.loads(text)
    except ValueError:
        return []
    out = []
    for row in (data.get("results") or [])[:10]:
        url = row.get("url") or ""
        if url:
            out.append((row.get("title") or url,
                        url,
                        row.get("description") or ""))
    return out


def _duckduckgo(query: str) -> list:
    """Only worth attempting over HTTP/2; urllib3's header set gets blocked."""
    global _last_ddg
    if time.time() - _last_ddg < config.DDG_MIN_INTERVAL:
        return []
    _last_ddg = time.time()
    # POST, not GET. A GET to /lite/ returns HTTP 202 with a CAPTCHA; the POST
    # form submission works, and works with plain requests -- no httpx needed.
    status, text = _post("https://lite.duckduckgo.com/lite/", {"q": query})
    if status == 202:
        _last_ddg = time.time() + config.DDG_BLOCK_COOLDOWN   # sticky block
        return []
    if status != 200 or "anomaly" in text[:4096].lower():
        return []

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(text, "html.parser")
    out = []
    for a in soup.select("a.result-link"):
        row = a.find_parent("tr")
        if row is not None:
            klass = row.get("class") or []
            if "result-sponsored" in klass:      # result #1 is otherwise an ad
                continue
        href = a.get("href") or ""
        if href.startswith("//duckduckgo.com/l/"):
            qs = parse_qs(urlsplit("https:" + href).query)
            href = unquote((qs.get("uddg") or [""])[0])
        if not href or (urlsplit(href).hostname or "").endswith("duckduckgo.com"):
            continue
        snippet = ""
        if row is not None:
            for sib in row.find_next_siblings("tr"):
                cell = sib.find("td", class_="result-snippet")
                if cell:
                    snippet = cell.get_text(" ", strip=True)
                    break
        out.append((a.get_text(" ", strip=True) or href, href, snippet))
    return out[:10]


def search(query: str) -> tuple[list, str]:
    """Return (results, backend_name). Results are (title, url, snippet)."""
    query = (query or "").strip()
    if not query:
        return [], ""
    hit = _cached(query)
    if hit is not None:
        return hit, "cache"

    # Order matters: wiby indexes only the small web and NEVER returns empty,
    # so putting it first made Marginalia and DuckDuckGo unreachable.
    for name, backend in (("DuckDuckGo", _duckduckgo),
                          ("Marginalia", _marginalia),
                          ("wiby", _wiby)):
        try:
            results = backend(query)
        except Exception:
            results = []
        if results:
            _store(query, results)
            return results, name
    return [], ""
