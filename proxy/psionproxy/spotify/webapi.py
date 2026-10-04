"""The Spotify Web API, as the PsionLX app needs it.

Login is OAuth 2.0 authorisation code with PKCE, against the user's own
Spotify developer app: no client secret exists anywhere. The app performs the
login (it has a browser); this process refreshes the token from then on and
writes it back, so there is one owner of the refresh token at a time.
"""

import base64
import hashlib
import secrets
import threading
import time
from urllib.parse import urlencode

import requests

from . import store

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
# Must be registered, exactly, in the Spotify developer dashboard. Spotify
# accepts plain http only for a loopback IP literal, not "localhost".
REDIRECT_PORT = 8897
REDIRECT_URI = f"http://127.0.0.1:{REDIRECT_PORT}/callback"
SCOPES = (
    "streaming user-read-email user-read-private "       # librespot signs in with this token
    "user-read-playback-state user-modify-playback-state user-read-currently-playing "
    "playlist-read-private playlist-read-collaborative user-library-read"
)


class SpotifyError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.message = message
        self.status = status


# --- PKCE login helpers (used by the app's "Log in to Spotify") -------------

def make_verifier() -> str:
    return secrets.token_urlsafe(64)[:96]


def challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def authorize_url(client_id: str, verifier: str, state: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "code_challenge_method": "S256",
        "code_challenge": challenge_for(verifier),
        "scope": SCOPES,
        "state": state,
    })


def exchange_code(client_id: str, code: str, verifier: str) -> dict:
    r = requests.post(TOKEN_URL, data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": client_id,
        "code_verifier": verifier,
    }, timeout=20)
    if r.status_code != 200:
        raise SpotifyError(f"Spotify refused the login ({r.status_code}): {r.text[:200]}",
                           r.status_code)
    data = r.json()
    return {
        "client_id": client_id,
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", ""),
        "scope": data.get("scope", ""),
        "expires_at": time.time() + int(data.get("expires_in", 3600)),
    }


# --- the API itself ------------------------------------------------------------

class WebAPI:
    def __init__(self):
        self._lock = threading.Lock()
        self._tok: dict | None = None
        self.session = requests.Session()

    def logged_in(self) -> bool:
        return store.load_token() is not None

    def token(self) -> str:
        """A valid access token, refreshing it if it is close to expiry."""
        with self._lock:
            tok = store.load_token()
            if tok is None:
                raise SpotifyError("Not logged in to Spotify. Log in from PsionNet on the Mac.", 401)
            if float(tok.get("expires_at", 0)) - time.time() < 120:
                tok = self._refresh(tok)
            self._tok = tok
            return tok["access_token"]

    def _refresh(self, tok: dict) -> dict:
        if not tok.get("refresh_token"):
            raise SpotifyError("The Spotify login has expired. Log in again from PsionNet.", 401)
        r = requests.post(TOKEN_URL, data={
            "grant_type": "refresh_token",
            "refresh_token": tok["refresh_token"],
            "client_id": tok["client_id"],
        }, timeout=20)
        if r.status_code != 200:
            raise SpotifyError("The Spotify login could not be renewed. Log in again from "
                               "PsionNet.", 401)
        data = r.json()
        tok = dict(tok)
        tok["access_token"] = data["access_token"]
        # PKCE refresh tokens rotate: keep the new one, or the old if none came.
        tok["refresh_token"] = data.get("refresh_token") or tok["refresh_token"]
        tok["expires_at"] = time.time() + int(data.get("expires_in", 3600))
        store.save_token(tok)
        return tok

    def call(self, method: str, path: str, params=None, body=None, retry=True):
        url = path if path.startswith("http") else API + path
        headers = {"Authorization": f"Bearer {self.token()}"}
        try:
            r = self.session.request(method, url, params=params, json=body,
                                     headers=headers, timeout=20)
        except requests.RequestException as exc:
            raise SpotifyError(f"Could not reach Spotify: {exc.__class__.__name__}")
        if r.status_code == 401 and retry:
            with self._lock:
                tok = store.load_token()
                if tok:
                    tok["expires_at"] = 0
                    store.save_token(tok)
            return self.call(method, path, params, body, retry=False)
        if r.status_code == 429 and retry:
            time.sleep(min(5, int(r.headers.get("Retry-After", "1") or 1)))
            return self.call(method, path, params, body, retry=False)
        if r.status_code == 204 or not r.content:
            return None
        if r.status_code >= 400:
            try:
                msg = r.json().get("error", {}).get("message", "")
            except ValueError:
                msg = r.text[:120]
            if r.status_code == 403 and "premium" in msg.lower():
                msg = "Spotify Premium is needed to control playback."
            raise SpotifyError(msg or f"Spotify said {r.status_code}", r.status_code)
        try:
            return r.json()
        except ValueError:
            return None

    # -- reading --
    def me(self):
        return self.call("GET", "/me")

    SEARCH_PAGE = 10    # Spotify's search refuses limit > 10 ("Invalid limit") since 2026

    def search(self, q: str, types: str = "track", limit: int = 20):
        """Up to `limit` results of each type, a page of ten at a time."""
        merged: dict = {}
        for offset in range(0, limit, self.SEARCH_PAGE):
            page = self.call("GET", "/search", {"q": q, "type": types, "offset": offset,
                                                "limit": min(self.SEARCH_PAGE, limit - offset)}) or {}
            full = False
            for key, part in page.items():
                items = (part or {}).get("items") or []
                merged.setdefault(key, {"items": []})["items"].extend(items)
                full = full or len(items) == self.SEARCH_PAGE
            if not full:
                break
        return merged

    def my_playlists(self, limit: int = 50):
        return self.call("GET", "/me/playlists", {"limit": limit})

    def playlist_tracks(self, playlist_id: str, limit: int = 100):
        # Spotify retired /playlists/{id}/tracks: by October 2026 it answers 403
        # Forbidden for every playlist. /items replaced it, and each entry holds
        # its song under "item" rather than "track".
        return self.call("GET", f"/playlists/{playlist_id}/items", {"limit": limit})

    def album(self, album_id: str):
        return self.call("GET", f"/albums/{album_id}")

    def liked(self, limit: int = 50):
        return self.call("GET", "/me/tracks", {"limit": limit})

    def player(self):
        return self.call("GET", "/me/player")

    def devices(self):
        return (self.call("GET", "/me/player/devices") or {}).get("devices", [])

    # -- control --
    def play(self, device_id: str, uris=None, context_uri=None, offset_uri=None):
        body = {}
        if context_uri:
            body["context_uri"] = context_uri
        if uris:
            body["uris"] = uris
        if offset_uri and (context_uri or (uris and len(uris) > 1)):
            body["offset"] = {"uri": offset_uri}
        return self.call("PUT", "/me/player/play", {"device_id": device_id}, body or None)

    def resume(self, device_id: str):
        return self.call("PUT", "/me/player/play", {"device_id": device_id})

    def pause(self, device_id: str):
        return self.call("PUT", "/me/player/pause", {"device_id": device_id})

    def next(self, device_id: str):
        return self.call("POST", "/me/player/next", {"device_id": device_id})

    def previous(self, device_id: str):
        return self.call("POST", "/me/player/previous", {"device_id": device_id})

    def volume(self, device_id: str, pct: int):
        return self.call("PUT", "/me/player/volume",
                         {"device_id": device_id, "volume_percent": max(0, min(100, pct))})

    def transfer(self, device_id: str, play: bool = True):
        return self.call("PUT", "/me/player", body={"device_ids": [device_id], "play": play})
