"""Single entry point for the bundled app.

A frozen bundle has no separate `python3` to spawn the proxy with, so the app
re-launches ITSELF with --run-proxy instead. Same binary, two modes.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _bundle_root() -> Path:
    """Where the payload lives, frozen or not."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def main() -> None:
    if "--run-proxy" in sys.argv:
        # The app reads this output as UTF-8. Windows would otherwise write its
        # own code page, and a song title outside it stops the proxy's log.
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
            except (AttributeError, ValueError):
                pass
        root = _bundle_root()
        sys.path.insert(0, str(root / "proxy"))
        from psionproxy import config
        args = sys.argv[1:]

        def opt(name, default=None):
            return args[args.index(name) + 1] if name in args else default

        config.BIND_HOST = opt("--host", config.BIND_HOST)
        config.BIND_PORT = int(opt("--port", config.BIND_PORT))
        config.FIDELITY = opt("--fidelity", config.FIDELITY)
        config.IMAGES_DEFAULT_ON = "--no-images" not in args
        config.apply_network_args(opt("--allow", ""),
                                  "--spotify" in args or "--spotify-demo" in args,
                                  "--spotify-demo" in args)
        config.apply_software_args("--software" in args, opt("--software-src", ""))
        if config.FIDELITY == "full":
            config.BUDGET_HTML_HARD = 110_000
        from psionproxy.app import main as proxy_main
        proxy_main()
        return

    from gui import main as gui_main
    gui_main()


if __name__ == "__main__":
    main()
