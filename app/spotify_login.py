"""Log in to Spotify from the Mac, for the PsionLX app.

OAuth 2.0 authorisation code with PKCE against the user's own Spotify
developer app: the browser does the sign-in, Spotify redirects to a one-shot
listener on 127.0.0.1:8897, and the code is exchanged for a token there. No
client secret exists. The token is written where the proxy's Spotify bridge
reads it; from then on the proxy refreshes it.

Nothing here touches Tk: results go into a queue the GUI drains.
"""

import http.server
import queue
import secrets
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def _proxy_path() -> None:
    """Make psionproxy importable, from source or from the frozen bundle."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parent.parent
    p = str(base / "proxy")
    if p not in sys.path:
        sys.path.insert(0, p)


_proxy_path()
from psionproxy.spotify import store, webapi  # noqa: E402

PAGE = """<!doctype html><meta charset="utf-8"><title>PsionNet</title>
<body style="font:15px -apple-system,Helvetica;margin:3em;color:#222">
<h2>{title}</h2><p>{body}</p></body>"""


class Login:
    def __init__(self, client_id: str):
        self.client_id = client_id.strip()
        self.results: queue.Queue = queue.Queue()     # (ok, message)
        self._server = None

    def start(self) -> None:
        verifier = webapi.make_verifier()
        state = secrets.token_urlsafe(24)
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                parts = urlsplit(self.path)
                if parts.path != "/callback":
                    self.send_error(404)
                    return
                q = parse_qs(parts.query)
                ok, msg = False, "The sign-in did not complete."
                if (q.get("state") or [""])[0] != state:
                    msg = "The sign-in reply did not match this request."
                elif q.get("error"):
                    msg = f"Spotify said: {q['error'][0]}"
                elif q.get("code"):
                    try:
                        tok = webapi.exchange_code(outer.client_id, q["code"][0], verifier)
                        store.save_token(tok)
                        ok, msg = True, "Logged in to Spotify."
                    except webapi.SpotifyError as exc:
                        msg = exc.message
                body = PAGE.format(
                    title="PsionNet is connected to Spotify" if ok else "Spotify sign-in failed",
                    body="You can close this tab. The netBook Pro's Spotify app is ready."
                    if ok else msg).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                outer.results.put((ok, msg))
                threading.Thread(target=outer._server.shutdown, daemon=True).start()

        try:
            self._server = http.server.HTTPServer(("127.0.0.1", webapi.REDIRECT_PORT), Handler)
        except OSError:
            self.results.put((False, f"Port {webapi.REDIRECT_PORT} on this Mac is in use, "
                                     "so the sign-in has nowhere to return to."))
            return
        threading.Thread(target=self._serve, daemon=True, name="spotify-login").start()
        webbrowser.open(webapi.authorize_url(self.client_id, verifier, state))

    def _serve(self) -> None:
        self._server.timeout = 300
        self._server.serve_forever()
        self._server.server_close()

    def cancel(self) -> None:
        if self._server is not None:
            threading.Thread(target=self._server.shutdown, daemon=True).start()


def logged_in() -> bool:
    return store.load_token() is not None


def log_out() -> None:
    store.forget_login()


def status() -> dict:
    return store.read_status()
