"""Unit tests for the transforms, independent of the network."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bs4 import BeautifulSoup

from psionproxy.extract import extract, fit_budget
from psionproxy.images import transcode
from psionproxy.sanitize import sanitize, serialize
from psionproxy.textmap import encode, psionise

FAILS = []


def ok(cond, label):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        FAILS.append(label)


# --- charset ---------------------------------------------------------------
ok(psionise("“q” — …") == '"q" -- ...', "smart punctuation downgraded")
ok(psionise("Łódź") == "Lódz", "L-stroke kept as a letter, not deleted")
ok(psionise("™") == "(tm)", "trademark not emitted as &trade; (ROM maps it to 'c')")
ok(psionise("café") == "café", "valid latin-1 preserved")
ok(psionise("Москва") == "??????", "untranslatable marked, not silently dropped")
ok(not any(0x80 <= b < 0xA0 for b in encode("“—™€…")), "no C1 bytes ever emitted")

# --- sanitiser -------------------------------------------------------------
s = sanitize(BeautifulSoup(
    '<div id=a class=b style=c><script>x()</script><span>keep</span>'
    '<strong>B</strong><a href="https://e.com/?a=1&b=2">L</a>'
    '<form><p>form content</p></form>'
    '<picture><source srcset=x.webp><img src="data:," data-src="/p.png"></picture></div>',
    "html.parser"), base="https://h.test/d/")
out = serialize(s)
ok("<script" not in out, "script killed")
ok("keep" in out and "<span" not in out, "span unwrapped, content kept")
ok("form content" in out, "form unwrapped, content kept (not decomposed)")
ok("<b>B</b>" in out, "strong renamed to b")
ok("https" not in out, "https rewritten to http")
ok("&amp;" in out, "ampersand escaped in href")
ok("/p.png" in out, "lazy-loaded data-src recovered and resolved")
ok(" id=" not in out and " class=" not in out and "style=" not in out, "id/class/style stripped")
ok("<br/>" not in out and "<img/>" not in out, "void elements unclosed (SGML style)")

# --- root-node sanitising (the bug that let BBC's <main id style> through) --
root = sanitize(BeautifulSoup('<main id="m" style="height:100%"><p>x</p></main>',
                              "html.parser").main, base="")
ok("id=" not in serialize(root) and "style=" not in serialize(root),
   "root node's own attributes cleaned")

# --- extraction ------------------------------------------------------------
art = ("<html><body><nav class=menu><a href=/x>X</a></nav>"
       "<article><h1>T</h1>" + "<p>" + "Prose sentence. " * 30 + "</p>" * 1 +
       "".join("<p>" + "More prose here. " * 20 + "</p>" for _ in range(3)) +
       "</article></body></html>")
body, is_art = extract(art, "http://t/")
ok(is_art and "Prose sentence." in body, "article extracted")
idx = "<html><body>" + "".join(f'<a href="/i{i}">Item {i}</a>' for i in range(30)) + "</body></html>"
body2, is_art2 = extract(idx, "http://t/", fidelity="lite")
ok((not is_art2) and body2.count("<li>") > 20, "lite mode falls back to link list")
body2m, _ = extract(idx, "http://t/", fidelity="medium")
ok(body2m.count("Item 2") > 0 and len(body2m) > 200, "medium mode keeps the whole body")
small, trunc = fit_budget(body2, hard=500)
ok(trunc and len(encode(small)) <= 500, "byte budget enforced")

# --- images ----------------------------------------------------------------
from PIL import Image, ImageDraw
import io
im = Image.new("RGBA", (1400, 900), (255, 0, 0, 128))
ImageDraw.Draw(im).rectangle([10, 10, 600, 400], fill=(0, 128, 255, 255))
buf = io.BytesIO(); im.save(buf, format="PNG")
res = transcode(buf.getvalue(), "x.png")
ok(res is not None, "PNG with alpha transcoded")
blob, mime, size = res
ok(mime in ("image/gif", "image/jpeg"), f"output is GIF or JPEG ({mime})")
ok(max(size) <= 500, "resized within the panel's viewport")
ok(len(blob) <= 16000, "within the per-image byte cap")
if mime == "image/gif":
    pos = 6 + 7
    packed = blob[10]
    if packed & 0x80:
        pos += 3 * (2 ** ((packed & 7) + 1))
    while pos < len(blob) and blob[pos] != 0x2C:
        if blob[pos] == 0x21:
            pos += 2
            while blob[pos]:
                pos += blob[pos] + 1
            pos += 1
        else:
            break
    ok(not (blob[pos + 9] & 0x40), "GIF is non-interlaced")
ok(transcode(b"junk") is None, "garbage input rejected cleanly")

print("\nALL PASS" if not FAILS else f"\n{len(FAILS)} FAILURES")
sys.exit(1 if FAILS else 0)
