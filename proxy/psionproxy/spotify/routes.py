"""The text API the PsionLX Spotify app reads, under /spotify/.

Every reply is UTF-8 text: a first line of "OK", or "ERR<TAB>message", then
one record per line with TAB-separated fields -- trivial to parse in C on a
400 MHz XScale, and nothing a 2005 userspace lacks.

  GET /spotify/hello                    OK, then state<TAB>ready|login|starting|error
  GET /spotify/status                   key<TAB>value lines: playing, here, title, ...
  GET /spotify/search?q=..&type=track   track|album|playlist rows
  GET /spotify/playlists                the user's playlists, Liked Songs first
  GET /spotify/tracks?uri=..            the tracks of a playlist, album or Liked Songs
  GET /spotify/play?uri=..&context=..   play a track (in its playlist/album), or a context
  GET /spotify/pause | resume | next | previous
  GET /spotify/volume?v=0..100
  GET /spotify/stream.mp3               the audio, MP3 128 kbit/s, never-ending
  GET /spotify/art?u=<cover url>&s=64   a cover image as a small baseline JPEG

Control and data endpoints require an "X-PsionNet-Client" request header. The
app sends it; a web page shown in the device's browser cannot, so a page
cannot drive the user's Spotify through an <img> or a link. The stream and
the art are served without it, because GStreamer fetches the stream.
"""

from urllib.parse import urlsplit

from flask import Response

from . import service
from .webapi import SpotifyError

TEXT = "text/plain; charset=utf-8"
_OPEN = {"hello", "stream.mp3", "art"}
_ART_HOSTS = (".scdn.co", ".spotifycdn.com")


def _clean(value) -> str:
    return str(value if value is not None else "").replace("\t", " ").replace("\r", " ") \
        .replace("\n", " ").strip()


def _text(lines, respond, status=200):
    body = "OK\n" + "".join("\t".join(_clean(f) for f in row) + "\n" for row in lines)
    return respond(body.encode("utf-8"), TEXT, status,
                   {"Cache-Control": "no-store", "Pragma": "no-cache"})


def _err(message: str, respond, status=200):
    return respond(f"ERR\t{_clean(message)}\n".encode("utf-8"), TEXT, status,
                   {"Cache-Control": "no-store", "Pragma": "no-cache"})


def _q(query, name, default=""):
    return (query.get(name) or [default])[0]


def _stream(svc):
    q = svc.out.subscribe()

    def gen():
        try:
            while True:
                yield q.get(timeout=30)
        except Exception:
            return
        finally:
            svc.out.unsubscribe(q)

    r = Response(gen(), mimetype="audio/mpeg", direct_passthrough=True)
    r.headers["Cache-Control"] = "no-cache"
    r.headers["Pragma"] = "no-cache"
    return r


def handle(route, query, request, respond):
    sub = route[len("/spotify/"):].strip("/")
    svc = service.get()
    if svc is None:
        return _err("The Spotify bridge is not running.", respond, 503)
    if sub not in _OPEN and not request.headers.get("X-PsionNet-Client"):
        return _err("This address is for the PsionLX Spotify app.", respond, 403)
    try:
        if sub == "hello":
            st, msg = svc.state()
            return _text([("state", st), ("message", msg), ("name", "PsionNet"),
                          ("version", "1")], respond)
        if sub == "stream.mp3":
            return _stream(svc)
        if sub == "art":
            url = _q(query, "u")
            host = (urlsplit(url).hostname or "").lower()
            if not url.startswith("https://") or not host.endswith(_ART_HOSTS):
                return _err("Not a Spotify cover image.", respond, 400)
            try:
                size = max(32, min(200, int(_q(query, "s", "64"))))
            except ValueError:
                size = 64
            return respond(svc.art(url, size), "image/jpeg", 200,
                           {"Cache-Control": "max-age=86400"})
        if sub == "status":
            st, msg = svc.state()
            if st != "ready":
                return _text([("state", st), ("message", msg)], respond)
            data = svc.status()
            return _text([("state", "ready")] + [(k, v) for k, v in data.items()], respond)
        if sub == "search":
            q = _q(query, "q").strip()
            if not q:
                return _text([], respond)
            return _text(svc.search(q, _q(query, "type", "track")), respond)
        if sub == "playlists":
            return _text(svc.playlists(), respond)
        if sub == "tracks":
            return _text(svc.tracks(_q(query, "uri")), respond)
        if sub == "play":
            svc.play(_q(query, "uri"), _q(query, "context"))
            return _text([], respond)
        if sub in ("pause", "resume", "next", "previous"):
            getattr(svc, sub)()
            return _text([], respond)
        if sub == "volume":
            try:
                svc.volume(int(_q(query, "v", "50")))
            except ValueError:
                return _err("Volume must be a number from 0 to 100.", respond)
            return _text([], respond)
    except SpotifyError as exc:
        return _err(exc.message, respond)
    except Exception as exc:                       # never a stack trace to the device
        print(f"spotify: {sub} failed: {exc!r}", flush=True)
        return _err("Something went wrong talking to Spotify.", respond)
    return _err("No such request.", respond, 404)
