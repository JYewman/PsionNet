"""Reduce a stylesheet to the rules an IE6-era engine can actually apply.

Pocket Internet Explorer on Windows CE 4.2 renders CSS -- the capability probe
served to a real netBook Pro confirmed both `style=` attributes and `<style>`
blocks paint. What it does not do is understand anything from the last twenty
years, and modern inline CSS is almost entirely that. Measured on the real
pages:

    BBC News     190,162 B of inline CSS
                 487 feature media queries, 1,056 rem units, 160 flex/grid
                 declarations, 90 calc(), 52 custom properties
    Wikipedia     10,182 B, far more conservative: no flex, no grid, no rem

Shipping BBC's stylesheet whole would cost 100 seconds at 19200 baud to
deliver rules the device discards on parse. Worse than useless: `display:flex`
is ignored, so a flex row collapses into a stack of full-width blocks, which
is what would have happened with no CSS at all, only slower.

So the stylesheet is filtered rather than budgeted. What survives is the
subset IE6 genuinely implements, which is also the subset that was designed
for screens this size in the first place: colours, fonts, borders, alignment,
spacing. That is most of what makes a page look like itself.

This is a tolerant text filter, not a conforming CSS parser. It errs towards
dropping anything it cannot confidently identify, because a dropped rule costs
appearance while a bad rule can cost legibility.
"""

import re

# Properties IE6 implements. Anything outside this set is dropped, which
# handles flex, grid, transform, transition, opacity, box-shadow and the rest
# without needing to name them.
SAFE_PROPERTIES = {
    "color", "background", "background-color", "background-image",
    "background-repeat", "background-position", "background-attachment",
    "font", "font-family", "font-size", "font-style", "font-weight",
    "font-variant", "line-height",
    "text-align", "text-decoration", "text-indent", "text-transform",
    "letter-spacing", "word-spacing", "white-space", "vertical-align",
    "direction",
    "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
    "padding", "padding-top", "padding-right", "padding-bottom",
    "padding-left",
    "border", "border-top", "border-right", "border-bottom", "border-left",
    "border-width", "border-style", "border-color", "border-collapse",
    "border-top-width", "border-right-width", "border-bottom-width",
    "border-left-width", "border-top-style", "border-right-style",
    "border-bottom-style", "border-left-style", "border-top-color",
    "border-right-color", "border-bottom-color", "border-left-color",
    "width", "height", "display", "float", "clear", "visibility", "overflow",
    "list-style", "list-style-type", "list-style-position",
    "list-style-image", "caption-side", "table-layout", "cursor",
}

# IE6 knows these display values and nothing else useful.
_SAFE_DISPLAY = {"block", "inline", "none", "list-item", "inline-block",
                 "table", "table-row", "table-cell"}

# Value-level constructs IE6 cannot evaluate. A declaration using any of them
# is dropped whole, since a half-understood value is worse than none.
_BAD_VALUE = re.compile(
    r"""\bvar\(|\bcalc\(|\brgba?\([^)]*\/|\bhsla?\(|
        \d\s*(?:rem|vh|vw|vmin|vmax|ch|ex)\b|
        \battr\(|\bclamp\(|\bmin\(|\bmax\(|
        \#[0-9a-fA-F]{8}\b|\bcurrentcolor\b|\btransparent\b""",
    re.VERBOSE | re.IGNORECASE)

# Selector syntax IE6 does not support. IE6 has type, class, id, descendant,
# grouping, universal, and the link pseudo-classes. It has no child or sibling
# combinators, no attribute selectors, no pseudo-elements, no :not/:nth.
_BAD_SELECTOR = re.compile(r"[>~\[\]]|\+|::|:not\(|:nth|:first-|:last-|"
                           r":only-|:checked|:focus-|:root|\|")
_SAFE_PSEUDO = re.compile(r":(?:hover|link|visited|active)\b", re.IGNORECASE)

# @media with a parenthesised feature is a media query, which IE6 ignores
# entirely -- it would drop the whole block, so we do too. Bare media types
# (screen, all, print) are understood and kept.
_MEDIA_FEATURE = re.compile(r"\(")

_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


# A reset rule zeroes margins and padding across a long list of bare element
# selectors, on the assumption that the design rules that follow will put the
# spacing back. Here most of those design rules have just been dropped as
# un-renderable, so keeping the reset would strip the browser's own default
# spacing and leave headings and paragraphs run together: visibly worse than
# no stylesheet at all. Both BBC News and the Guardian open with one.
_RESET_DECL = re.compile(r"^(?:margin|padding|border|outline|font-size|"
                         r"font-weight|font-style|list-style|vertical-align|"
                         r"background|font)$")


def _is_reset_rule(selectors: str, declarations: str) -> bool:
    sels = [x.strip() for x in selectors.split(",") if x.strip()]
    if len(sels) < 8:
        return False
    # Bare element selectors only: a reset targets tag names, not classes.
    if any(("." in x or "#" in x or " " in x or ":" in x) for x in sels):
        return False
    for decl in declarations.split(";"):
        prop, _, value = decl.partition(":")
        if not prop.strip():
            continue
        if not _RESET_DECL.match(prop.strip().lower()):
            return False
        v = value.strip().lower().rstrip("0123456789.px% ")
        if v not in ("", "none", "normal", "baseline", "inherit", "transparent"):
            return False
    return True


# Reset stylesheets also strip the browser's own semantic formatting one
# element at a time, in rules too short for _is_reset_rule to catch: bullets
# off lists, underlines off links, boldness off headings. A modern design puts
# those cues back by other means, usually colour and spacing we have just
# dropped. On a small monochrome-ish screen with no other affordances, an
# underlined link and a bulleted list are how you can tell what they are, so
# these are refused on bare element selectors.
_KEEP_DEFAULTS = (
    ({"ol", "ul", "li", "menu", "dir"}, "list-style", {"none", "0"}),
    ({"ol", "ul", "li", "menu", "dir"}, "list-style-type", {"none"}),
    ({"a"}, "text-decoration", {"none"}),
    ({"h1", "h2", "h3", "h4", "h5", "h6", "b", "strong"},
     "font-weight", {"normal", "400"}),
    ({"i", "em", "cite"}, "font-style", {"normal"}),
    ({"body", "html", "p", "li", "td"}, "line-height", {"1", "1.0", "0"}),
    ({"table"}, "border-collapse", set()),
)


def _strips_default_semantics(selectors: str, prop: str, value: str) -> bool:
    """True if this declaration removes a default cue the device relies on."""
    sels = {x.strip().lower() for x in selectors.split(",") if x.strip()}
    if not sels or any(("." in x or "#" in x or " " in x or ":" in x)
                       for x in sels):
        return False                    # scoped rule: the author meant it
    v = value.strip().lower()
    for elements, bad_prop, bad_values in _KEEP_DEFAULTS:
        if prop == bad_prop and sels <= elements and bad_values and v in bad_values:
            return True
    return False


def _split_blocks(css: str):
    """Yield (prelude, body, is_at_rule) at the top level of a stylesheet."""
    depth = start = 0
    prelude = None
    for i, ch in enumerate(css):
        if ch == "{":
            if depth == 0:
                prelude = css[start:i]
                body_start = i + 1
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                yield prelude.strip(), css[body_start:i], prelude.lstrip().startswith("@")
                start = i + 1
            elif depth < 0:          # stray brace; resynchronise
                depth = 0
                start = i + 1


def _filter_declarations(body: str, selectors: str = "") -> str:
    """Keep only declarations IE6 implements."""
    out = []
    for decl in body.split(";"):
        if ":" not in decl:
            continue
        prop, _, value = decl.partition(":")
        prop = prop.strip().lower()
        value = value.strip()
        if not prop or not value:
            continue
        if prop.startswith("--") or prop.startswith("-"):
            continue                        # custom properties, vendor prefixes
        if prop not in SAFE_PROPERTIES:
            continue
        if _BAD_VALUE.search(value):
            continue
        if prop == "display" and value.lower() not in _SAFE_DISPLAY:
            continue                        # flex, grid, contents, ...
        if selectors and _strips_default_semantics(selectors, prop, value):
            continue
        if "!important" in value.lower():
            value = re.sub(r"\s*!\s*important", "", value, flags=re.IGNORECASE)
        out.append(f"{prop}:{value.strip()}")
    return ";".join(out)


def _filter_selectors(prelude: str) -> str:
    """Drop selectors using syntax IE6 cannot match."""
    keep = []
    for sel in prelude.split(","):
        sel = " ".join(sel.split())
        if not sel:
            continue
        probe = _SAFE_PSEUDO.sub("", sel)   # a:hover is fine; :focus-within is not
        if _BAD_SELECTOR.search(probe):
            continue
        keep.append(sel)
    return ",".join(keep)


def filter_for_ie6(css: str, budget: int = 0) -> str:
    """Return the IE6-renderable subset of `css`, at most `budget` bytes.

    `budget` of 0 means no limit. Rules are kept in source order, so the
    cascade is preserved among whatever survives.
    """
    if not css:
        return ""
    css = _COMMENT.sub("", css)
    out = []
    size = 0

    def emit(chunk: str) -> bool:
        nonlocal size
        if budget and size + len(chunk) > budget:
            return False
        out.append(chunk)
        size += len(chunk)
        return True

    def rules(block: str):
        for prelude, body, is_at in _split_blocks(block):
            if is_at:
                name = prelude.split()[0].lower() if prelude.split() else ""
                if name != "@media" or _MEDIA_FEATURE.search(prelude):
                    continue            # @supports, @keyframes, @font-face, queries
                inner = "".join(rules(body))
                if inner:
                    yield "@media " + prelude[6:].strip() + "{" + inner + "}"
                continue
            sel = _filter_selectors(prelude)
            if not sel:
                continue
            decls = _filter_declarations(body, sel)
            if not decls:
                continue
            if _is_reset_rule(sel, decls):
                continue
            yield sel + "{" + decls + "}"

    for rule in rules(css):
        if not emit(rule):
            break
    return "".join(out)
