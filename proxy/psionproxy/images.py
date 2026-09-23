"""Transcode arbitrary web images to something an ER5 ROM can actually decode.

The ROM registers exactly two image decoders -- Gif (offset 7,098,959) and
Jpeg (7,115,683). 'image/png' occurs zero times in the whole 16 MB ROM, so a
PNG produces the dead-end "Web cannot open this file" save dialog.

Pillow's defaults are wrong for this target in two specific ways: it writes
interlaced GIFs, and progressive JPEG is reported as "Not supported" on the
device. Both are forced off below.
"""

import io

from PIL import Image

from . import config

Image.MAX_IMAGE_PIXELS = 40_000_000   # fed arbitrary bytes from the open web

_PHOTO_EXT = (".jpg", ".jpeg", ".webp", ".avif", ".heic")


def _flatten(im: Image.Image) -> Image.Image:
    """Composite onto opaque white.

    The ROM's GIF decoder has a documented speckling bug and transparency is
    not worth the risk on a 256-colour panel.
    """
    if im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info:
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im)
    return im.convert("RGB")


def _looks_photographic(im: Image.Image, url: str) -> bool:
    if url.lower().split("?")[0].endswith(_PHOTO_EXT):
        return True
    sample = im.convert("RGB").resize((64, 64))
    return len(sample.getcolors(maxcolors=4096) or []) > 700


def transcode(data: bytes, url: str = "",
              long_edge: int = config.IMAGE_LONG_EDGE,
              cap: int = config.BUDGET_IMAGE):
    """Return (bytes, mime, (w, h)) or None if it cannot be made to fit.

    Returning None matters: the caller then emits a numbered link instead of a
    broken image, rather than shipping something over budget with no signal.
    """
    try:
        im = Image.open(io.BytesIO(data))
        im.seek(0)                      # first frame only; never animate
        im.load()
    except Exception:
        return None

    try:
        im = _flatten(im)
    except Exception:
        return None

    photo = _looks_photographic(im, url)
    long_edge = min(long_edge, config.IMAGE_LONG_EDGE_MAX)

    best = None
    for edge in (long_edge, int(long_edge * 0.75), int(long_edge * 0.55), 240, 180):
        if edge < 60:
            break
        w, h = im.size
        if max(w, h) > edge:
            scale = edge / float(max(w, h))
            size = (max(1, int(w * scale)), max(1, int(h * scale)))
            resample = Image.LANCZOS if photo else Image.BOX
            frame = im.resize(size, resample)
        else:
            frame = im
            size = frame.size

        for quality in (70, 55, 40, 30):
            cand = _encode(frame, photo, quality)
            if cand is None:
                continue
            blob, mime = cand
            if len(blob) <= cap:
                return blob, mime, size
            if best is None or len(blob) < len(best[0]):
                best = (blob, mime, size)
            if not photo:
                break          # quality is meaningless for the GIF path
    return None


def _encode(frame: Image.Image, photo: bool, quality: int):
    """Encode both ways where sensible and keep the smaller result."""
    out = []
    if photo:
        buf = io.BytesIO()
        try:
            frame.save(buf, format="JPEG", quality=quality,
                       optimize=True, progressive=False)   # MANDATORY: no progressive
            out.append((buf.getvalue(), "image/jpeg"))
        except Exception:
            pass
    buf = io.BytesIO()
    try:
        pal = frame.convert("P", palette=Image.ADAPTIVE, colors=256)
        pal.save(buf, format="GIF", interlace=False)       # MANDATORY: no interlace
        out.append((buf.getvalue(), "image/gif"))
    except Exception:
        pass
    if not out:
        return None
    return min(out, key=lambda pair: len(pair[0]))
