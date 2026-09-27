"""Downgrade arbitrary modern HTML to the HTML 3.2 subset an ER5 browser parses.

The whitelists below are not guesswork. They were read out of the Series 7 ROM
image itself: the STNC "HTML Type Converter" registers its element table at
offset 7,080,368 and its attribute table at 7,084,000. Anything absent from
those tables is unknown to the parser, so we either delete it or unwrap it.

Note there is no `id`, no `class`, no `style` and no `title` attribute in the
ROM's table. Fragment links therefore have to be <a name="x">, not <h2 id="x">.
"""

import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, NavigableString, Tag
from bs4.dammit import EntitySubstitution
from bs4.formatter import HTMLFormatter

from .textmap import psionise

DOCTYPE = '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">'

# Elements the ROM's converter knows. Emit nothing outside this set.
ALLOWED = {
    "a", "address", "b", "big", "blockquote", "body", "br",
    "caption", "center", "cite", "code", "dd", "dfn", "dir", "div", "dl", "dt",
    "em", "font", "form", "h1", "h2", "h3", "h4", "h5", "h6", "head", "hr",
    "html", "i", "img", "input", "isindex", "kbd", "li", "map", "area", "menu",
    "meta", "ol", "option", "p", "pre", "samp", "select", "small", "strike",
    "strong", "sub", "sup", "table", "td", "textarea", "th", "title", "tr",
    "tt", "u", "ul", "var",
}

# Delete the element AND everything inside it.
KILL = {
    "script", "noscript", "style", "link", "iframe", "frame", "frameset",
    "noframes", "object", "embed", "applet", "param", "svg", "canvas", "video",
    "audio", "source", "track", "template", "slot", "dialog",
    "progress", "meter", "datalist", "base", "basefont",
}

# Delete the tag but keep its children. `picture` and `form` MUST be here:
# decomposing them deletes every responsive image and every form-wrapped
# content region on the page (verified against bs4).
UNWRAP = {
    "span", "label", "q", "abbr", "acronym", "del", "ins", "s", "bdo", "nobr",
    "wbr", "article", "section", "main", "nav", "header", "footer", "aside",
    "figure", "figcaption", "details", "summary", "mark", "time", "data",
    "output", "fieldset", "legend", "optgroup", "thead", "tbody", "tfoot",
    "colgroup", "col", "picture", "ruby", "rt", "rp", "hgroup",
    "search", "menuitem",
}

RENAME = {"strong": "b", "em": "i", "cite": "i", "var": "i", "dfn": "i",
          "samp": "tt", "kbd": "tt", "code": "tt", "address": "i",
          "strike": "u", "s": "u", "sub": "small", "sup": "small"}

# The ROM's own attribute table, verbatim.
ATTR_OK = {
    "href", "name", "rel", "rev", "target", "codebase", "alt", "width",
    "height", "vspace", "hspace", "align", "shape", "coords", "size",
    "bgcolor", "text", "vlink", "alink", "background", "clear", "nowrap",
    "rowspan", "colspan", "valign", "compact", "src", "color", "action",
    "method", "enctype", "marginwidth", "marginheight", "scrolling",
    "noresize", "frameborder", "bordercolor", "rows", "cols", "border",
    "noshade", "usemap", "ismap", "type", "value", "checked", "maxlength",
    "accept", "prompt", "http-equiv", "content", "start", "selected",
    "language", "multiple", "cellspacing", "cellpadding",
}

# bs4 formatter that escapes only & < > " and leaves void elements unclosed.
# 'minimal' would re-add <br/>; 'html' would emit the fatal &mdash; and &trade;.
FORMATTER = HTMLFormatter(
    entity_substitution=EntitySubstitution.substitute_xml,
    void_element_close_prefix="",
)

_WS = re.compile(r"[ \t\r\n\f\v]+")

# HTML 3.2 fixes %InputType to exactly this set. Anything else (type="search",
# type="email"...) must be downgraded or the ROM sees an invalid enumerated value.
_INPUT_TYPES = {"text", "password", "checkbox", "radio", "submit", "reset",
                "file", "hidden", "image"}
# Attributes HTML 3.2 declares as enumerated-with-no-value. bs4 serialises a
# minimised boolean as selected="" and '' is not a legal value for these.
_BOOLEAN_ATTRS = ("selected", "checked", "multiple", "compact", "nowrap",
                  "noshade", "ismap", "noresize")
_LABEL_ATTRS = ("placeholder", "aria-label", "title")

# Kept in step with config.IMAGE_LONG_EDGE; imported lazily to avoid a cycle.
_IMG_LONG_EDGE = 400


def fix_forms(soup) -> None:
    """Make forms submittable on ER5. Must run BEFORE the attribute filter.

    The Series 7 User Guide has a section titled "Using forms", illustrated with
    a working AltaVista search box, so the device genuinely submits forms. Every
    problem here is ours.
    """
    # A search box is often named ONLY by placeholder/aria-label/title, all of
    # which the attribute filter strips. Harvest a visible label first, and pop
    # the source attributes so a second pass cannot duplicate it.
    for field in soup.find_all(["input", "textarea", "select"]):
        attrs = getattr(field, "attrs", None) or {}
        label = ""
        for key in _LABEL_ATTRS:
            if attrs.get(key):
                label = label or str(attrs[key]).strip()
            attrs.pop(key, None)
        if label and (attrs.get("type", "text") or "text").lower() not in ("hidden", "submit", "reset"):
            field.insert_before(NavigableString(psionise(label) + " "))

    # <button> is not in the ROM's element table. Wikipedia's search form has
    # NO <input type=submit> and 12 <button> elements -- deleting them leaves
    # the form with no way to submit at all.
    for btn in soup.find_all("button"):
        btype = (btn.get("type") or "submit").lower()
        if btype == "button":
            btn.decompose()
            continue
        sub = _FACTORY.new_tag("input")
        sub["type"] = "submit" if btype != "reset" else "reset"
        text = btn.get_text(" ", strip=True)[:40]
        if text:
            sub["value"] = psionise(text)
        if btn.get("name"):
            sub["name"] = btn["name"]
        btn.replace_with(sub)

    for form in soup.find_all("form"):
        # fetch() is GET-only, so a POST form has nothing to submit to.
        form["method"] = "get"
        form.attrs.pop("enctype", None)
        form.attrs.pop("target", None)   # in ATTR_OK, but the ROM ignores it

        # No upload path exists, and the FAQ reports Web 2.00 REFUSES to submit
        # a form whose file field holds an invalid path -- so leaving an empty
        # one in place breaks the whole form.
        for f in form.find_all("input", attrs={"type": "file"}):
            f.decompose()

        for sel in form.find_all("select"):
            _flatten_options(sel)

        # Guarantee something to submit with. Hacker News has one form, no
        # <input type=submit> and no <button> -- it relies on Enter-in-field,
        # which no source attests for WTL 2.0.
        has_submit = any(
            (i.get("type") or "text").lower() in ("submit", "image")
            for i in form.find_all("input"))
        if not has_submit and form.find(["input", "select", "textarea"]):
            go = _FACTORY.new_tag("input")
            go["type"] = "submit"
            go["value"] = "Go"
            form.append(go)


def _flatten_options(select) -> None:
    """Rebuild a <select>'s options flat.

    html.parser applies no implied end tags, so '<option>One<option>Two'
    parses as option 2 NESTED INSIDE option 1 -- the two merge into a single
    entry and the serialiser ships stacked '</option></option>'.
    """
    harvested = []
    for opt in select.find_all("option"):
        own = "".join(str(c) for c in opt.children
                      if isinstance(c, NavigableString)).strip()
        harvested.append((own or opt.get_text(" ", strip=True),
                          opt.get("value"),
                          opt.has_attr("selected")))
    for child in list(select.children):
        child.extract()
    for text, value, selected in harvested[:30]:      # a country list is 250+
        if not text:
            continue
        opt = _FACTORY.new_tag("option")
        if value is not None:
            opt["value"] = value
        if selected:
            opt["selected"] = None
        opt.string = psionise(text)[:60]
        select.append(opt)


def _http_only(url: str) -> str:
    """Rewrite a URL so the Psion never sees a scheme it cannot handle.

    'https' occurs zero times in the entire 16 MB ROM -- there is no https
    scheme handler, and the failure mode is a dead-end error dialog.
    """
    if not url:
        return url
    u = url.strip()
    if u.startswith("//"):
        u = "http:" + u
    elif u.lower().startswith("https://"):
        u = "http://" + u[8:]
    low = u.lower()
    if low.startswith(("javascript:", "data:", "vbscript:", "blob:", "about:")):
        return ""
    # http://host:443/ is a real redirect target in the wild; strip the port.
    parts = urlsplit(u)
    if parts.scheme == "http" and parts.netloc.endswith(":443"):
        u = urlunsplit(parts._replace(netloc=parts.netloc[:-4]))
    return u


def _clean_attrs(tag: Tag, base: str, profile=None) -> None:
    from . import profiles
    profile = profile or profiles.DEFAULT
    if not getattr(tag, "attrs", None):
        return
    allowed = ATTR_OK
    if profile.keep_css:
        # Everything except event handlers and framework noise. CSS renders on
        # this client, so class/id/style earn their bytes.
        allowed = None
    for key in list(tag.attrs):
        low = key.lower()
        drop = (low.startswith("on") or low.startswith("data-")
                if allowed is None
                else (low.startswith(("data-", "aria-", "on")) or low not in allowed))
        if drop:
            del tag.attrs[key]
            continue
        val = tag.attrs[low]
        if isinstance(val, list):
            val = " ".join(val)
        if low == "type" and tag.name == "input":
            val = str(val).lower()
            if val not in _INPUT_TYPES:
                val = "text"          # type=search / email / url -> text
        if low == "size" and tag.name == "input":
            # Google ships size="57"; at ~9 px/char that is 410 px and pushes
            # the submit button off the 546 px line.
            try:
                val = str(min(int(str(val)), 30))
            except ValueError:
                val = "20"
        if low in ("href", "src", "action", "background"):
            val = _http_only(urljoin(base, str(val).strip()) if base else str(val))
            if not val:
                del tag.attrs[low]
                continue
        if low in _BOOLEAN_ATTRS:
            tag.attrs[low] = None     # serialises bare, per HTML 3.2
            continue
        cleaned = psionise(str(val))
        if not cleaned.strip():
            # target="" and friends: an empty value for an enumerated
            # attribute has no legal reading. Drop the attribute.
            del tag.attrs[low]
            continue
        tag.attrs[low] = cleaned


def _resolve_image_src(tag: Tag, base: str) -> None:
    """Modern lazy-loading means src is often a 1x1 placeholder.

    Recover a real URL from srcset/data-src before the attribute filter throws
    them away, taking the SMALLEST srcset candidate since bandwidth is the
    binding constraint.
    """
    src = (tag.get("src") or "").strip()
    placeholder = (not src) or src.startswith("data:") or "blank." in src or "1x1" in src
    if placeholder:
        for alt_key in ("data-src", "data-lazy-src", "data-original", "data-srcset"):
            cand = (tag.get(alt_key) or "").strip()
            if cand:
                src = cand.split(",")[0].strip().split()[0]
                break
        else:
            ss = (tag.get("srcset") or "").strip()
            if ss:
                src = ss.split(",")[0].strip().split()[0]
    if src:
        tag["src"] = urljoin(base, src) if base else src


def sanitize(soup: BeautifulSoup, base: str = "", profile=None) -> BeautifulSoup:
    """Reduce a parsed document to what the client can render.

    With a profile that keeps CSS (Windows CE), <style> blocks and style
    attributes survive and the element whitelist is not applied -- that device
    renders far more than the ER5 ROM and flattening it to HTML 3.2 would
    throw away formatting it could have shown.
    """
    from . import profiles
    profile = profile or profiles.DEFAULT

    kill = set(KILL)
    if profile.keep_css:
        kill.discard("style")
        kill.discard("link")
    for tag in soup.find_all(list(kill)):
        tag.decompose()

    fix_forms(soup)
    for form in soup.find_all("form"):
        action = (form.get("action") or "").strip()
        form["action"] = _http_only(urljoin(base, action) if base else action) or (base or "/")

    for tag in soup.find_all("img"):
        _resolve_image_src(tag, base)

    for tag in soup.find_all(list(UNWRAP)):
        tag.unwrap()

    for tag in soup.find_all(True):
        name = tag.name.lower()
        if profile.strict_html32:
            if name in RENAME:
                tag.name = RENAME[name]
                name = tag.name
            if name not in ALLOWED:
                tag.unwrap()
                continue
        _clean_attrs(tag, base, profile)

    # An <a> with no href is dead weight; an <img> with no src is a broken icon.
    for tag in soup.find_all("a"):
        if not tag.get("href"):
            tag.unwrap()
    for tag in soup.find_all("img"):
        src = tag.get("src") or ""
        if not src:
            tag.decompose()
            continue
        # No SVG decoder in the ROM, and Pillow cannot rasterise it either.
        if src.split("?")[0].lower().endswith((".svg", ".svgz")):
            alt = (tag.get("alt") or "").strip()
            tag.replace_with(NavigableString(psionise(alt)) if alt else "")
            continue
        # The transcoder caps the long edge, so declared dimensions taken from
        # the original would stretch the smaller image. Scale them to match.
        try:
            w, h = int(tag.get("width", 0)), int(tag.get("height", 0))
        except (TypeError, ValueError):
            w = h = 0
        if w and h and max(w, h) > _IMG_LONG_EDGE:
            scale = _IMG_LONG_EDGE / float(max(w, h))
            tag["width"], tag["height"] = str(int(w * scale)), str(int(h * scale))

    # find_all() returns DESCENDANTS only, so the node we were handed still
    # carries its own name and attributes. An extracted <main id=... style=...>
    # would otherwise sail straight through.
    root_name = getattr(soup, "name", None)
    if root_name and root_name not in ("[document]", "html", "body"):
        if root_name in RENAME:
            soup.name = RENAME[root_name]
        elif root_name not in ALLOWED:
            soup.name = "div"
        _clean_attrs(soup, base)

    for node in soup.find_all(string=True):
        raw = str(node)
        text = raw if node.parent and node.parent.name == "pre" else _WS.sub(" ", raw)
        # Only flatten to the CP1252/ISO-8859-1 intersection where the client
        # needs it. Windows CE renders UTF-8, and downgrading would replace
        # every curly quote and dash for no reason.
        node.replace_with(NavigableString(psionise(text) if profile.downgrade_text else text))
    return soup


_FACTORY = BeautifulSoup("", "html.parser")


def horizontalize_menus(soup) -> None:
    """Turn short link-only lists into one ' | ' separated line.

    Borrowed in spirit from 68kproxy's horizontalizeMenu. This is the single
    biggest vertical-space win on index pages, where nav lists dominate.
    """
    for lst in soup.find_all(["ul", "ol"]):
        items = lst.find_all("li", recursive=False)
        if not items or len(items) > 12:
            continue
        links = []
        for li in items:
            first = next((c for c in li.children if isinstance(c, Tag)), None)
            if first is None or first.name != "a":
                break
            links.append(first)
        else:
            if sum(len(l.get_text()) for l in links) < 160:
                p = _FACTORY.new_tag("p")
                for i, link in enumerate(links):
                    if i:
                        p.append(NavigableString(" | "))
                    p.append(link.extract())
                lst.replace_with(p)


def render(title: str, body_html: str, extra_head: str = "", profile=None) -> str:
    """Wrap a body fragment in a complete document for this client."""
    from . import profiles
    profile = profile or profiles.DEFAULT
    shown = psionise(title) if profile.downgrade_text else title
    return (
        f"{profile.doctype}\n<html><head>"
        f'<meta http-equiv="Content-Type" content="text/html; charset={profile.charset}">'
        f"<title>{shown}</title>{extra_head}</head>\n"
        f"<body bgcolor=\"#ffffff\" text=\"#000000\">\n{body_html}\n</body></html>\n"
    )


def serialize(node) -> str:
    """Serialise with the ER5-safe formatter, then repair parser artefacts.

    Python's html.parser does not apply implied end tags, so '<p>a<p>b' parses
    as nested paragraphs. Flatten that rather than shipping the nesting.
    """
    html = node.decode(formatter=FORMATTER)
    html = re.sub(r"</p>\s*</p>", "</p>", html)
    html = re.sub(r"<p>\s*</p>", "", html)
    html = re.sub(r"(?:<br>\s*){3,}", "<br><br>", html)
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html


def serialize_contents(node) -> str:
    """Serialise a node's CHILDREN, discarding the wrapper element itself."""
    html = node.decode_contents(formatter=FORMATTER)
    html = re.sub(r"</p>\s*</p>", "</p>", html)
    html = re.sub(r"<p>\s*</p>", "", html)
    html = re.sub(r"(?:<br>\s*){3,}", "<br><br>", html)
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html
