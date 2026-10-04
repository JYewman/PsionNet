"""The Spotify bridge: one instance per proxy process.

Rows handed to routes.py are tuples of plain strings, one per line of the
text API:

  ("track",    uri, title, artist, album, duration_ms, art_url)
  ("album",    uri, name, artist, year, art_url)
  ("playlist", uri, name, owner, track_count, art_url)
"""

import io
import threading
import time

import requests

from . import store
from .audio import Broadcaster, Pump
from .librespot import DEVICE_NAME, Librespot
from .webapi import SpotifyError, WebAPI

LIKED = "spotify:liked"          # pseudo-URI for the user's Liked Songs

_service = None


def get():
    return _service


def start() -> None:
    """Called once from the proxy's main() when Spotify is enabled."""
    global _service
    from .. import config
    if config.SPOTIFY_DEMO:
        from .demo import DemoService
        _service = DemoService()
    else:
        _service = SpotifyService()
    _service.start()


def _art(images) -> str:
    """The smallest cover image of at least 64 px."""
    best = ""
    best_w = 10 ** 9
    for im in images or []:
        w = im.get("width") or 300
        if 64 <= w < best_w:
            best, best_w = im.get("url", ""), w
    if not best and images:
        best = images[0].get("url", "")
    return best


def _artists(obj) -> str:
    return ", ".join(a.get("name", "") for a in obj.get("artists", []) if a.get("name"))


def _play_whole(uri: str) -> tuple:
    """One row standing for a playlist whose songs Spotify will not list."""
    return ("track", uri, "Play this whole playlist",
            "Spotify only lists the songs of your own playlists", "", "0", "")


def track_row(t: dict, album: dict | None = None) -> tuple:
    album = album or t.get("album") or {}
    return ("track", t.get("uri", ""), t.get("name", ""), _artists(t),
            album.get("name", ""), str(t.get("duration_ms", 0)), _art(album.get("images")))


class SpotifyService:
    def __init__(self):
        self.api = WebAPI()
        self.out = Broadcaster()
        self.player = Librespot(log=self._log)
        self.pump = Pump(self.player.audio_source, self.out)
        self._device_id = ""
        self._user = ""
        self._status_cache = (0.0, None)
        self._art_cache: dict[str, bytes] = {}
        self._lock = threading.Lock()

    def _log(self, line: str) -> None:
        print(f"spotify: {line}", flush=True)

    def stop(self) -> None:
        """The proxy is stopping: take the speaker down with it."""
        self.player.stop()
        self.pump.stop()

    def start(self) -> None:
        self.pump.start()
        threading.Thread(target=self._status_loop, daemon=True, name="spotify-status").start()
        threading.Thread(target=self._wait_for_login, daemon=True, name="spotify-login").start()

    def _wait_for_login(self) -> None:
        """librespot needs the token, so it starts once the user has logged in."""
        while not self.api.logged_in():
            time.sleep(2)
        self.player.start()

    # -- state --------------------------------------------------------------
    def state(self) -> tuple[str, str]:
        if not self.api.logged_in():
            return "login", "Log in to Spotify in PsionNet on the Mac."
        if self.player.state == "login":
            return "login", self.player.message
        if self.player.state == "failed":
            return "error", self.player.message or "The Spotify player could not start."
        if not self.device_id(refresh=False):
            return "starting", "Starting the netBook Pro speaker..."
        return "ready", ""

    def device_id(self, refresh: bool = True) -> str:
        if self._device_id or not refresh:
            return self._device_id
        try:
            for d in self.api.devices():
                if d.get("name") == DEVICE_NAME and d.get("id"):
                    self._device_id = d["id"]
                    break
        except SpotifyError:
            pass
        return self._device_id

    def _need_device(self) -> str:
        dev = self.device_id()
        if not dev:
            raise SpotifyError("The netBook Pro speaker is not online yet. Give it a few "
                               "seconds after logging in.")
        return dev

    def _status_loop(self) -> None:
        while True:
            try:
                if self.api.logged_in() and not self._user:
                    me = self.api.me() or {}
                    self._user = me.get("display_name") or me.get("id") or ""
                self.device_id()
            except SpotifyError:
                pass
            st, msg = self.state()
            store.write_status(state=st, message=msg, user=self._user,
                               device=DEVICE_NAME, device_ready=bool(self._device_id),
                               player=self.player.state, listeners=self.out.listeners)
            time.sleep(3)

    # -- reading ------------------------------------------------------------
    def status(self) -> dict:
        when, cached = self._status_cache
        if cached is not None and time.time() - when < 1.5:
            return cached
        p = self.api.player() or {}
        item = p.get("item") or {}
        album = item.get("album") or {}
        dev = p.get("device") or {}
        st = {
            "playing": "1" if p.get("is_playing") else "0",
            "here": "1" if dev.get("id") and dev.get("id") == self._device_id else "0",
            "device": dev.get("name", ""),
            "track": item.get("uri", ""),
            "title": item.get("name", ""),
            "artist": _artists(item),
            "album": album.get("name", ""),
            "art": _art(album.get("images")),
            "progress": str(p.get("progress_ms") or 0),
            "duration": str(item.get("duration_ms") or 0),
            "volume": str(dev.get("volume_percent") if dev.get("volume_percent") is not None else ""),
            "shuffle": "1" if p.get("shuffle_state") else "0",
            "repeat": p.get("repeat_state", "off"),
        }
        self._status_cache = (time.time(), st)
        return st

    def search(self, q: str, kind: str = "track") -> list[tuple]:
        kind = kind if kind in ("track", "album", "playlist") else "track"
        res = self.api.search(q, kind, 20) or {}
        rows = []
        for t in (res.get("tracks") or {}).get("items", []) or []:
            if t:
                rows.append(track_row(t))
        for a in (res.get("albums") or {}).get("items", []) or []:
            if a:
                rows.append(("album", a.get("uri", ""), a.get("name", ""), _artists(a),
                             (a.get("release_date") or "")[:4], _art(a.get("images"))))
        for p in (res.get("playlists") or {}).get("items", []) or []:
            if p:
                rows.append(self._playlist_row(p))
        return rows

    def _playlist_row(self, p: dict) -> tuple:
        owner = (p.get("owner") or {}).get("display_name", "")
        # "items" since Spotify's 2026 playlist change; "tracks" before it
        count = (p.get("items") or p.get("tracks") or {}).get("total", "")
        return ("playlist", p.get("uri", ""), p.get("name", ""), owner, str(count),
                _art(p.get("images")))

    def playlists(self) -> list[tuple]:
        rows = []
        try:
            liked = self.api.liked(1) or {}
            rows.append(("playlist", LIKED, "Liked Songs", self._user or "you",
                         str(liked.get("total", "")), ""))
        except SpotifyError:
            pass
        for p in (self.api.my_playlists() or {}).get("items", []) or []:
            if p:
                rows.append(self._playlist_row(p))
        return rows

    def tracks(self, uri: str) -> list[tuple]:
        if uri == LIKED:
            items = (self.api.liked(50) or {}).get("items", [])
            return [track_row(i["track"]) for i in items if i and i.get("track")]
        kind, _, ident = uri.rpartition(":")
        if kind.endswith("playlist"):
            try:
                page = self.api.playlist_tracks(ident) or {}
            except SpotifyError as exc:
                if exc.status != 403:
                    raise
                # Since 2026 Spotify shows an app with a developer Client ID the
                # songs of the user's OWN playlists only; for one they follow it
                # answers 403. It will still play it, so offer that instead.
                return [_play_whole(uri)]
            rows = []
            for entry in page.get("items", []) or []:
                song = (entry or {}).get("item") or (entry or {}).get("track")
                if song and song.get("type") == "track":     # not podcast episodes
                    rows.append(track_row(song))
            return rows
        if kind.endswith("album"):
            album = self.api.album(ident) or {}
            items = (album.get("tracks") or {}).get("items", [])
            return [track_row(t, album) for t in items if t]
        raise SpotifyError("Not a playlist or album.")

    # -- control ------------------------------------------------------------
    def play(self, uri: str, context: str = "") -> None:
        dev = self._need_device()
        if context == LIKED:
            uris = [r[1] for r in self.tracks(LIKED)]
            self.api.play(dev, uris=uris or [uri], offset_uri=uri if uri in uris else None)
        elif context and (":playlist:" in context or ":album:" in context):
            # offset only at a song: the "play this whole playlist" row's URI is
            # the playlist itself
            self.api.play(dev, context_uri=context,
                          offset_uri=uri if uri.startswith("spotify:track:") else None)
        elif uri.startswith("spotify:track:"):
            self.api.play(dev, uris=[uri])
        else:
            self.api.play(dev, context_uri=uri)
        self._status_cache = (0.0, None)

    def resume(self) -> None:
        dev = self._need_device()
        st = self.status()
        if st.get("here") == "1" or not st.get("track"):
            self.api.resume(dev)
        else:
            self.api.transfer(dev, True)       # pick up where the phone left off
        self._status_cache = (0.0, None)

    def pause(self) -> None:
        self.api.pause(self._need_device())
        self._status_cache = (0.0, None)

    def next(self) -> None:
        self.api.next(self._need_device())
        self._status_cache = (0.0, None)

    def previous(self) -> None:
        self.api.previous(self._need_device())
        self._status_cache = (0.0, None)

    def volume(self, pct: int) -> None:
        self.api.volume(self._need_device(), pct)
        self._status_cache = (0.0, None)

    # -- cover art ----------------------------------------------------------
    def art(self, url: str, size: int) -> bytes:
        key = f"{size}:{url}"
        hit = self._art_cache.get(key)
        if hit:
            return hit
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        from PIL import Image
        im = Image.open(io.BytesIO(r.content)).convert("RGB")
        im.thumbnail((size, size), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=82, progressive=False)
        blob = buf.getvalue()
        if len(self._art_cache) > 200:
            self._art_cache.clear()
        self._art_cache[key] = blob
        return blob
