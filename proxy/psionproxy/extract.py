"""Content extraction, because raw pages do not fit down a 115200 baud pipe.

BBC News is 1,033,530 decompressed bytes -- 92 seconds of PPP time before the
StrongARM starts parsing. Extraction is not an optimisation here, it is the
difference between usable and not.

Two modes, and the fallback is the common case rather than an edge case:
readability-style scoring returns ~780 bytes for BBC News but ~11 characters
for Hacker News, because index pages have no "article". So the link-index mode
is built first and used whenever scoring comes back thin.
"""

import re
from bs4 import BeautifulSoup, Tag

from .sanitize import horizontalize_menus, sanitize, serialize, serialize_contents
from .sanitize import settle_css, drop_dead_css_hooks
from . import config

# Containers whose class/id names betray boilerplate. Checked BEFORE the
# attribute filter strips those names off.
# Word-boundaried, and deliberately WITHOUT "promo": the BBC names every
# headline block "gs-c-promo", so that one token was deleting the news.
# Measured: BBC News went from 9 of 103 headlines to 100 of 103 by dropping it.
_JUNK = re.compile(
    r"(?:^|[\s_-])(?:"
    r"disqus|share-?(?:bar|button|tools)|social-?(?:bar|links|share)|"
    r"advert|advertisement|sponsored|cookie-?(?:banner|notice|consent)|consent-?banner|"
    r"newsletter-?signup|subscribe-?(?:box|form)|paywall|"
    r"skip-?link|breadcrumb|pagination|masthead|popup|modal|lightbox|"
    r"site-?footer|page-?footer|global-?footer"
    r")(?:[\s_-]|$)",
    re.I,
)
_POSITIVE = re.compile(r"article|content|post|story|entry|main-?body|prose|text", re.I)

_BLOCK_TAGS = ("p", "pre", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6",
               "ul", "ol", "dl", "table")


def strip_boilerplate(soup: BeautifulSoup) -> None:
    """Drop obvious chrome before scoring, using class/id while they still exist."""
    for tag in soup.find_all(True):
        if tag.name in ("html", "body", "head"):
            continue
        # bs4 hands back nodes whose .attrs is None (doctypes, declarations).
        attrs = getattr(tag, "attrs", None)
        if not attrs:
            continue
        klass = attrs.get("class") or ""
        ident = " ".join(filter(None, [
            " ".join(klass) if isinstance(klass, list) else str(klass),
            str(attrs.get("id") or ""),
            str(attrs.get("role") or ""),
        ]))
        if ident and _JUNK.search(ident) and not _POSITIVE.search(ident):
            tag.decompose()


def _score(tag: Tag) -> float:
    """Text density: characters of prose, penalised by link-heaviness."""
    text = tag.get_text(" ", strip=True)
    if not text:
        return 0.0
    link_chars = sum(len(a.get_text(" ", strip=True)) for a in tag.find_all("a"))
    link_ratio = link_chars / max(len(text), 1)
    paras = len(tag.find_all("p"))
    return len(text) * (1.0 - min(link_ratio, 0.95)) + paras * 25


def _depth(tag: Tag) -> int:
    d = 0
    while tag.parent is not None:
        d += 1
        tag = tag.parent
    return d


def find_article(soup: BeautifulSoup):
    """Pick the densest prose container, or None if the page is an index.

    Two failure modes to avoid, and they pull in opposite directions:
      - picking a whole-page wrapper (Ars Technica's `main#main` scores highest
        but contains the entire page, blowing the byte budget), and
      - rejecting the real article on a clean article page, where the article
        legitimately IS most of the document.
    So rather than a flat "reject anything over 60% of the page" rule, score
    every candidate and then descend into the tightest child that retains
    essentially all of the score.
    """
    body = soup.body or soup
    if len(body.get_text(" ", strip=True)) < 200:
        return None

    scored = []
    for tag in body.find_all(["article", "div", "section", "main", "td"]):
        if len(tag.get_text(" ", strip=True)) < 200:
            continue
        if len(tag.find_all(_BLOCK_TAGS)) < 2:
            continue
        s = _score(tag)
        if s > 0:
            scored.append((s, tag))
    if not scored:
        return None

    best_score, best = max(scored, key=lambda pair: (pair[0], _depth(pair[1])))

    # Descend: if a descendant keeps ~all of the score in fewer bytes, it is the
    # real article and the outer node was a wrapper carrying nav and related links.
    changed = True
    while changed:
        changed = False
        for s, tag in scored:
            if tag is best or best not in tag.parents and tag not in best.descendants:
                continue
            if tag in best.descendants and s >= best_score * 0.9:
                best, best_score, changed = tag, s, True
                break

    if len(best.get_text(" ", strip=True)) < 400:
        return None
    return best


def link_index(soup: BeautifulSoup, base: str) -> str:
    """Render an index page as a compact, de-duplicated list of links."""
    seen, rows = set(), []
    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        label = a.get_text(" ", strip=True)
        if not href or not label or len(label) < 3:
            continue
        if href.startswith("#"):
            continue
        key = href.split("#")[0]
        if key in seen:
            continue
        seen.add(key)
        if len(label) > 110:
            label = label[:107] + "..."
        rows.append(f'<li><a href="{href}">{label}</a></li>')
    if not rows:
        return "<p><i>No readable content or links found on this page.</i></p>"
    return "<ul>\n" + "\n".join(rows) + "\n</ul>"


def extract(html: str, base: str, fidelity: str = "", profile=None) -> tuple[str, bool]:
    """Return (body_html, was_article).

    Default is MEDIUM fidelity: the whole body, pruned and cleaned, with NO
    readability extraction. Extraction was throwing away most of the page to
    hit a 24 KB cap that the link never actually required.

    fidelity="lite" restores the old extract-the-article behaviour.
    """
    from . import config
    from .prune import collapse_divs, prune_hidden

    fidelity = fidelity or config.FIDELITY
    soup = BeautifulSoup(html, "html.parser")
    # Keep <style> for clients that render CSS -- stripping it here would undo
    # the profile decision before sanitize() ever sees it.
    strip = ["script", "noscript", "template"]
    if not (profile and profile.keep_css):
        strip.append("style")
    for tag in soup.find_all(strip):
        tag.decompose()

    prune_hidden(soup)
    strip_boilerplate(soup)
    if fidelity != "lite":
        demote_chrome(soup)

    if fidelity == "lite":
        article = find_article(soup)
        if article is not None:
            sanitize(article, base, profile)
            horizontalize_menus(article)
            collapse_divs(article)
            settle_css(article, profile)
            drop_dead_css_hooks(article, profile)
            return serialize_contents(article), True
        sanitize(soup, base, profile)
        horizontalize_menus(soup)
        return link_index(soup, base), False

    # medium / full: keep the whole body.
    sanitize(soup, base, profile)
    horizontalize_menus(soup)
    collapse_divs(soup)
    if config.RELATIVISE_URLS:
        relativise(soup, base)

    # Order matters. settle_css() must run before <body> is selected, so head
    # blocks are relocated into it rather than silently dropped, and
    # drop_dead_css_hooks() must run after, so it judges class names against
    # the CSS that will actually be sent.
    settle_css(soup, profile)
    body = soup.body or soup
    drop_dead_css_hooks(body, profile)
    rendered = serialize_contents(body) if getattr(body, "name", None) else serialize(soup)
    if len(rendered.strip()) < 200:
        # Nothing survived -- fall back to a link index rather than a blank page.
        return link_index(soup, base), False
    return rendered, True


_CHROME_HINT = re.compile(
    r"(?:^|[\s_-])(?:nav|navigation|navbar|menu|sidebar|site-?header|"
    r"page-?header|mw-panel|mw-navigation|vector-(?:menu|header|toc)|"
    r"toolbar|footer|site-?info|catlinks)(?:[\s_-]|$)", re.I)


def demote_chrome(soup) -> int:
    """Move navigation and footers BELOW the content, in a smaller font.

    MediaWiki and most CMSes emit the whole sidebar and menu tree before the
    article, so on a device with no CSS you scroll through every menu before
    reaching a word of content. The chrome is still useful -- it just belongs
    at the bottom.

    Runs before sanitize(), while class/id/role still exist to identify it.
    """
    body = soup.body or soup
    if getattr(body, "name", None) is None:
        return 0
    moved = []
    for tag in list(body.find_all(["nav", "header", "footer", "div", "aside", "ul"])):
        if tag.parent is None or tag is body:
            continue
        semantic = tag.name in ("nav", "header", "footer", "aside")
        attrs = getattr(tag, "attrs", None) or {}
        klass = attrs.get("class") or ""
        ident = " ".join([
            " ".join(klass) if isinstance(klass, list) else str(klass),
            str(attrs.get("id") or ""), str(attrs.get("role") or "")])
        if not (semantic or _CHROME_HINT.search(ident)):
            continue
        if tag.find("h1") or tag.find("h2"):
            continue          # real content lives here; leave it alone
        text = tag.get_text(" ", strip=True)
        if len(text) > 3000:
            continue          # too big to be chrome
        moved.append(tag.extract())

    if not moved:
        return 0
    rule = _FACTORY_TAG("hr")
    body.append(rule)
    holder = _FACTORY_TAG("font")
    holder["size"] = "1"
    for tag in moved:
        holder.append(tag)
    body.append(holder)
    return len(moved)


def _FACTORY_TAG(name):
    from .sanitize import _FACTORY
    return _FACTORY.new_tag(name)


def relativise(soup, base: str) -> None:
    """Shorten same-origin URLs to paths.

    Lossless, and worth more bytes than any styling transformation: measured
    -22.3% on Hacker News, -15.7% on Wikipedia, -9.0% on the Guardian.
    hrefs are 26-48% of final output bytes.
    """
    if not base:
        return
    from urllib.parse import urlsplit
    origin = urlsplit(base)
    prefix = f"{origin.scheme}://{origin.netloc}"
    alt = f"http://{origin.netloc}"
    for tag in soup.find_all(["a", "img", "form"]):
        for attr in ("href", "src", "action"):
            val = tag.get(attr)
            if not val:
                continue
            for p in (prefix, alt):
                if val.startswith(p + "/"):
                    tag[attr] = val[len(p):]
                    break


_TRAILING_LI = re.compile(r"<li>(?:(?!</li>).)*</li>\s*(?=</ul>|</ol>|$)", re.S)


def fit_budget(body_html: str,
               hard: int = config.BUDGET_HTML_HARD) -> tuple[str, bool]:
    """Shed trailing list items until the body fits. Returns (html, truncated)."""
    from .textmap import encode
    if len(encode(body_html)) <= hard:
        return body_html, False
    out, guard = body_html, 0
    while len(encode(out)) > hard and guard < 4000:
        new = _TRAILING_LI.sub("", out, count=1)
        if new == out:
            break
        out, guard = new, guard + 1
    if len(encode(out)) > hard:
        # Still too big: hard-truncate at a tag boundary.
        raw = encode(out)[:hard]
        cut = raw.rfind(b"><")
        out = raw[: cut + 1].decode("latin-1", "ignore") if cut > 0 else raw.decode("latin-1", "ignore")
    return out, True
