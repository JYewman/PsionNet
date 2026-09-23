"""Delete markup the page itself says is invisible.

This is the single biggest byte win available, and it needs no CSS engine.

Measured across real pages, `display` is the most common CSS declaration on the
modern web -- 14.2% of all declarations. But running a real cascade to resolve
it was tried and rejected: an indexed hide-only cascade destroyed 99 genuine
phrases on the Guardian front page, because sites routinely style content in
ways a partial engine misreads.

Attribute-driven pruning is the safe subset. It only deletes what the markup
declares outright -- `hidden`, `aria-hidden="true"`, and an inline
`style="display:none"` -- never anything inferred from a stylesheet.

Measured: BBC News 97,347 -> 53,150 bytes (-45%) while keeping 53 of 55 unique
headlines. On BBC the bulk of that is four near-identical 43 KB `[hidden]`
copies of the same top-story block, shipped for different breakpoints -- so
this is deduplication, not content loss.
"""

import re

_INLINE_HIDDEN = re.compile(
    r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\s*(?:;|$)", re.I)

# Floor guard: if pruning would gut the page, it misread it -- put it back.
_MIN_TEXT_AFTER = 400
_MIN_TEXT_BEFORE = 2_000


def _is_hidden(tag) -> bool:
    attrs = getattr(tag, "attrs", None)
    if not attrs:
        return False
    if "hidden" in attrs:
        return True
    if str(attrs.get("aria-hidden", "")).lower() == "true":
        return True
    style = attrs.get("style")
    if style and _INLINE_HIDDEN.search(str(style)):
        return True
    if str(attrs.get("type", "")).lower() == "hidden" and tag.name == "input":
        return False        # hidden form fields must survive; they get submitted
    return False


def prune_hidden(soup) -> int:
    """Remove hidden subtrees in place. Returns bytes removed."""
    body = soup.body or soup
    before_text = len(body.get_text(" ", strip=True))
    before_bytes = len(str(soup))

    hidden = [t for t in soup.find_all(True) if _is_hidden(t)]
    if not hidden:
        return 0

    # Reduce to top-level subtrees so we decompose each region once.
    hidden_set = set(id(t) for t in hidden)
    roots = [t for t in hidden
             if not any(id(p) in hidden_set for p in t.parents)]

    snapshot = str(soup) if before_text > _MIN_TEXT_BEFORE else None
    for tag in roots:
        # Never delete a region that carries a form -- search boxes live in
        # exactly the kind of collapsed nav drawer this would otherwise remove.
        if tag.find("form") or tag.name in ("html", "body"):
            continue
        tag.decompose()

    after_text = len((soup.body or soup).get_text(" ", strip=True))
    if snapshot is not None and after_text < _MIN_TEXT_AFTER:
        # We misread the page. Revert wholesale rather than ship a blank.
        from bs4 import BeautifulSoup
        restored = BeautifulSoup(snapshot, "html.parser")
        soup.clear()
        for child in list(restored.children):
            soup.append(child.extract())
        return 0

    return before_bytes - len(str(soup))


_COLLAPSIBLE = {"div", "section", "article", "main", "center"}


def collapse_divs(soup, max_passes: int = 6) -> int:
    """Unwrap single-child wrapper divs, repeatedly, to a fixed point.

    Measured -0.7% to -13.9% of bytes, but the bigger prize is element count:
    this roughly halves what a 132 MHz StrongARM has to lay out.
    """
    removed = 0
    for _ in range(max_passes):
        changed = False
        for tag in list(soup.find_all(list(_COLLAPSIBLE))):
            if tag.parent is None:
                continue
            kids = [c for c in tag.children if getattr(c, "name", None)]
            texts = "".join(str(c) for c in tag.children
                            if not getattr(c, "name", None)).strip()
            # A wrapper adds nothing if it holds one element and no own text,
            # and carries no attributes worth keeping.
            if len(kids) == 1 and not texts and not (getattr(tag, "attrs", None) or {}):
                tag.unwrap()
                removed += 1
                changed = True
        if not changed:
            break
    return removed
