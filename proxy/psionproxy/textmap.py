"""Unicode -> ISO-8859-1 downgrade for EPOC ER5.

Why this module exists, and why it is the most important one:

ER5 is an 8-bit, pre-Unicode OS. The Series 7 User Guide (Appendices p.203) states
its character set is "the IBM Code Page 1252 character set". The ROM browser holds
no charset conversion table at all -- it writes response bytes straight into a
CP1252 font. Opera 5.14 for ER5, meanwhile, does not even recognise the label
"windows-1252"; it knows iso-8859-1, us-ascii, utf-8, utf-16 and macintosh.

The only byte range where both browsers agree is where CP1252 and ISO-8859-1
agree: 0x09/0x0A/0x0D, 0x20-0x7E and 0xA0-0xFF. Bytes 0x80-0x9F are smart
punctuation under CP1252 but C1 control characters under ISO-8859-1, so emitting
them renders correctly on one browser and as garbage on the other.

So: transliterate everything outside that range, and encode with latin-1 --
never cp1252, which would emit exactly the 0x80-0x9F bytes we must avoid.
"""

import unicodedata

# Characters that must never reach the encoder, with their ASCII stand-ins.
# Smart punctuation is the common case: it lives at 0x80-0x9F in CP1252.
_EXPLICIT = {
    # quotes and apostrophes
    "‘": "'", "’": "'", "‚": ",", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "′": "'", "″": '"', "‵": "'", "‶": '"',
    "«": '"', "»": '"',   # guillemets: valid latin-1 but unreadable on a small screen
    # dashes and hyphens
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "--", "―": "--", "−": "-", "­": "",
    # spaces (all collapse to a plain space; NBSP 0xA0 is valid and kept)
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
    " ": " ", "　": " ", "​": "", "‌": "", "‍": "",
    "﻿": "",
    # punctuation and symbols
    "…": "...", "•": "*", "‣": "*", "▪": "*", "●": "*",
    "·": "·",           # middle dot IS valid latin-1 -- keep it
    "†": "+", "‡": "++", "‰": " o/oo",
    "™": "(tm)",             # NEVER &trade;: the ROM maps it to 0x63, a stray "c"
    "€": "EUR", "₤": "£", "₽": "RUB", "₹": "INR",
    "№": "No.", "℅": "c/o",
    # arrows and maths
    "→": "->", "←": "<-", "↔": "<->", "⇒": "=>", "⇐": "<=",
    "↑": "^", "↓": "v",
    "≤": "<=", "≥": ">=", "≠": "!=", "≈": "~", "∞": "inf",
    "×": "×", "÷": "÷",   # both valid latin-1 and in the ROM table
    "⁄": "/", "∕": "/",
    # ligatures the NFKD pass would otherwise mangle
    "Œ": "OE", "œ": "oe", "Æ": "Æ", "æ": "æ",
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
    "Š": "S", "š": "s", "Ž": "Z", "ž": "z", "Ÿ": "Ü",
    "ƒ": "f",
    # Latin letters with no NFKD decomposition -- without these they vanish entirely
    "Ł": "L", "ł": "l",   # L-stroke:  "Lodz" not "odz"
    "Đ": "D", "đ": "d",
    "Ħ": "H", "ħ": "h",
    "ı": "i", "ĸ": "k",
    "Ŋ": "N", "ŋ": "n",
    "Ŧ": "T", "ŧ": "t",
    "Ə": "e", "ſ": "s",
    "Ð": "Ð", "Þ": "Þ", "þ": "þ",  # valid latin-1
    # typographic junk that appears constantly in scraped copy
    "‹": "<", "›": ">", "ˆ": "^", "˜": "~",
    " ": " ",
}

_LATIN1_OK = set(range(0xA0, 0x100))


def _one(ch: str) -> str:
    """Downgrade a single character to something latin-1 can carry."""
    if ch in _EXPLICIT:
        return _EXPLICIT[ch]
    o = ord(ch)
    if o < 0x80:
        return ch
    if o in _LATIN1_OK:
        return ch
    # 0x80-0x9F: real C1 controls in the source. Drop them.
    if 0x80 <= o < 0xA0:
        return ""
    # Try to strip accents down to a base letter: "Ω" -> "O", "ā" -> "a".
    decomposed = unicodedata.normalize("NFKD", ch)
    kept = "".join(c for c in decomposed if not unicodedata.combining(c))
    if kept and kept != ch:
        out = "".join(_one(c) for c in kept)
        if out:
            return out
    # Decorative symbols and emoji (Unicode category "So") have no latin-1
    # equivalent and carry no meaning -- a row of "?" is worse than nothing.
    # Anything genuinely in latin-1 (degree sign, plus-minus) was returned
    # above, and the ones worth keeping are in _EXPLICIT.
    if unicodedata.category(ch) in ("So", "Sk", "Cf"):
        return ""
    # Otherwise leave a visible mark rather than silently deleting a letter --
    # "Mockba" should not become "".
    return "?"


def psionise(text: str) -> str:
    """Map arbitrary Unicode onto the CP1252/ISO-8859-1 safe intersection."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    if text.isascii():
        return text
    return "".join(_one(c) for c in text)


def encode(text: str) -> bytes:
    """Final encode. Guarantees no byte lands in the 0x80-0x9F danger zone."""
    raw = psionise(text).encode("latin-1", errors="replace")
    if any(0x80 <= b < 0xA0 for b in raw):
        raw = bytes(b if not (0x80 <= b < 0xA0) else 0x3F for b in raw)
    return raw
