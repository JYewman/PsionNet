"""A stand-in for Spotify, for testing the app and the audio path.

Canned search results and playlists, and a "player" that streams a test tone
through the same Pump, encoder and Broadcaster the real one uses -- so
everything but Spotify itself is exercised. Enabled with --spotify-demo.
"""

import io
import math
import os
import struct
import threading
import time

from . import store
from .audio import Broadcaster, Pump, BYTES_PER_SEC
from .service import LIKED

_TRACKS = [
    ("spotify:track:demo1", "Psion Organiser Blues", "The Teklogix Five", "Series 7", 187000),
    ("spotify:track:demo2", "EPOC Release Five", "The Teklogix Five", "Series 7", 214000),
    ("spotify:track:demo3", "Netbook Nights", "Matchbox", "GPE Sessions", 241000),
    ("spotify:track:demo4", "StrongARM Shuffle", "Matchbox", "GPE Sessions", 199000),
    ("spotify:track:demo5", "Four Hundred Megahertz", "XScale", "PXA255", 263000),
]


# Covers on a Spotify image host, so the app's request passes the same host
# check as a real one; art() draws them rather than fetching anything.
_ART = "https://i.scdn.co/image/demo-cover-{}"
_COLOURS = [(28, 42, 74), (138, 148, 168), (18, 104, 52), (160, 24, 24), (196, 150, 40)]


def _cover(t):
    return _ART.format(t[0].rsplit(":", 1)[-1])


def _row(t):
    return ("track", t[0], t[1], t[2], t[3], str(t[4]), _cover(t))


class DemoService:
    def __init__(self):
        self.out = Broadcaster()
        self._r, self._w = os.pipe()
        os.set_blocking(self._w, False)
        self.pump = Pump(lambda: self._r, self.out)
        self.playing = False
        self.index = 0
        self.position = 0.0
        self.volume_pct = 70
        self._last = time.monotonic()

    def start(self) -> None:
        self.pump.start()
        threading.Thread(target=self._tone, daemon=True, name="demo-tone").start()
        threading.Thread(target=self._status, daemon=True, name="demo-status").start()

    def _status(self):
        while True:
            store.write_status(state="ready", message="demo", user="demo",
                               device="netBook Pro", device_ready=True, player="demo",
                               listeners=self.out.listeners)
            time.sleep(3)

    def _tone(self):
        """Write PCM like librespot does: as fast as the pipe takes it."""
        phase = 0
        while True:
            if not self.playing:
                time.sleep(0.05)
                continue
            freq = 330 + 110 * self.index
            amp = int(9000 * self.volume_pct / 100)
            frames = []
            for _ in range(2205):
                v = int(amp * math.sin(2 * math.pi * freq * phase / 44100))
                frames.append(struct.pack("<hh", v, v))
                phase += 1
            data = b"".join(frames)
            while data and self.playing:
                # Write it all: a full pipe takes part of a write (on Windows,
                # any part), and a lost remainder would misalign every sample
                # after it.
                try:
                    n = os.write(self._w, data)
                except BlockingIOError:
                    n = 0
                data = data[n:]
                if n == 0:
                    time.sleep(0.02)

    def _tick(self):
        now = time.monotonic()
        if self.playing:
            self.position += now - self._last
            if self.position * 1000 >= _TRACKS[self.index][4]:
                self.index, self.position = (self.index + 1) % len(_TRACKS), 0.0
        self._last = now

    def state(self):
        return "ready", ""

    def status(self):
        self._tick()
        t = _TRACKS[self.index]
        return {"playing": "1" if self.playing else "0", "here": "1",
                "device": "netBook Pro", "track": t[0], "title": t[1], "artist": t[2],
                "album": t[3], "art": _cover(t), "progress": str(int(self.position * 1000)),
                "duration": str(t[4]), "volume": str(self.volume_pct),
                "shuffle": "0", "repeat": "off"}

    def search(self, q, kind="track"):
        q = q.lower()
        hits = [t for t in _TRACKS if q in (t[1] + " " + t[2] + " " + t[3]).lower()] or _TRACKS
        return [_row(t) for t in hits]

    def playlists(self):
        return [("playlist", LIKED, "Liked Songs", "demo", str(len(_TRACKS)), ""),
                ("playlist", "spotify:playlist:demo", "Psion Classics", "demo", "3", "")]

    def tracks(self, uri):
        return [_row(t) for t in (_TRACKS if uri == LIKED else _TRACKS[:3])]

    def play(self, uri, context=""):
        self._tick()
        for i, t in enumerate(_TRACKS):
            if t[0] == uri:
                self.index = i
        self.position, self.playing = 0.0, True

    def resume(self):
        self._tick(); self.playing = True

    def pause(self):
        self._tick(); self.playing = False

    def next(self):
        self._tick(); self.index, self.position = (self.index + 1) % len(_TRACKS), 0.0

    def previous(self):
        self._tick(); self.index, self.position = (self.index - 1) % len(_TRACKS), 0.0

    def volume(self, pct):
        self.volume_pct = max(0, min(100, pct))

    def art(self, url, size):
        """A drawn cover, through the same thumbnail and JPEG path as a real one."""
        from PIL import Image, ImageDraw
        n = next((i for i, t in enumerate(_TRACKS) if _cover(t) == url), None)
        if n is None:
            raise ValueError("not a demo cover")
        im = Image.new("RGB", (300, 300), _COLOURS[n % len(_COLOURS)])
        d = ImageDraw.Draw(im)
        d.ellipse((60, 60, 240, 240), outline=(255, 255, 255), width=14)
        d.ellipse((132, 132, 168, 168), fill=(255, 255, 255))
        im.thumbnail((size, size), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=82, progressive=False)
        return buf.getvalue()
