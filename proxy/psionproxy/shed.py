"""Fit a page to its byte budget by dropping whole low-value blocks.

Replaces the old fit_budget(), which was the single largest Mac-side cost in
the proxy: it re-encoded the entire document to latin-1 on each of up to 4,000
regex iterations -- measured at 1,665 ms on the Guardian front page -- and then
fell back to a blind mid-document byte cut.

This works on the tree instead, removes whole semantic units, and measures once
per batch rather than once per item.
"""

from .textmap import encode

# Dropped in this order. Earliest = least missed.
_SHED_ORDER = ("footer", "nav", "aside", "form", "table", "ul", "ol", "dl")


def _size(node) -> int:
    return len(encode(str(node)))


def _link_density(tag) -> float:
    text = tag.get_text(" ", strip=True)
    if not text:
        return 1.0
    links = sum(len(a.get_text(" ", strip=True)) for a in tag.find_all("a"))
    return links / max(len(text), 1)


def shed_to_budget(soup, hard: int) -> tuple[bool, int]:
    """Shrink the document in place. Returns (was_shed, final_bytes)."""
    size = _size(soup)
    if size <= hard:
        return False, size

    body = soup.body or soup
    shed_any = False

    # Pass 1: link-dense blocks, from the END of the document backwards.
    # Navigation and related-links clusters live at the tail and read as noise.
    for name in _SHED_ORDER:
        candidates = [t for t in body.find_all(name) if t.parent is not None]
        for tag in reversed(candidates):
            if size <= hard:
                break
            if tag.find("form") and name != "form":
                continue
            if name in ("ul", "ol", "dl") and _link_density(tag) < 0.6:
                continue          # a genuine content list, not a nav block
            cost = _size(tag)
            if cost < 200:
                continue
            tag.decompose()
            size -= cost
            shed_any = True
        if size <= hard:
            break

    # Pass 2: still too big -- drop trailing top-level blocks until it fits.
    if size > hard:
        blocks = [c for c in body.children if getattr(c, "name", None)]
        for tag in reversed(blocks):
            if size <= hard:
                break
            cost = _size(tag)
            tag.decompose()
            size -= cost
            shed_any = True

    return shed_any, _size(soup)
