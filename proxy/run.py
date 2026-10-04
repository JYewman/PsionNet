#!/usr/bin/env python3
"""Entry point: python3 run.py [--host H] [--port N] [--images]"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from psionproxy import config  # noqa: E402

parser = argparse.ArgumentParser(description="PsionNet downgrading proxy")
parser.add_argument("--host", default=config.BIND_HOST,
                    help="bind address (default: the Mac's PPP-side address)")
parser.add_argument("--port", type=int, default=config.BIND_PORT)
parser.add_argument("--fidelity", choices=("lite", "medium", "full"),
                    default=config.FIDELITY,
                    help="how much of each page to keep (default: medium)")
parser.add_argument("--max-images", type=int, default=None,
                    help="max images inlined per page (default 4); "
                         "raise it to find this device's ceiling")
parser.add_argument("--no-images", action="store_true",
                    help="defer images to numbered links instead of inlining")
parser.add_argument("--allow", default="",
                    help="network mode: comma-separated networks allowed to use "
                         "the proxy, e.g. 192.168.1.0/24 (loopback always is)")
parser.add_argument("--spotify", action="store_true",
                    help="serve the Spotify bridge for the PsionLX app")
parser.add_argument("--spotify-demo", action="store_true",
                    help="Spotify bridge with canned data and a test tone")
parser.add_argument("--software", action="store_true",
                    help="serve PsionLX-Software at /lx/, for PsionLX's Find new software")
parser.add_argument("--software-src", default="",
                    help="where PsionLX-Software is: its URL, or a local folder to test")
args = parser.parse_args()

config.BIND_HOST = args.host
config.BIND_PORT = args.port
config.IMAGES_DEFAULT_ON = not args.no_images
if args.max_images is not None:
    config.BUDGET_IMAGES_PER_PAGE = max(0, args.max_images)
config.FIDELITY = args.fidelity
config.apply_network_args(args.allow, args.spotify or args.spotify_demo, args.spotify_demo)
config.apply_software_args(args.software, args.software_src)
if args.fidelity == "full":
    config.BUDGET_HTML_HARD = 110_000

from psionproxy.app import main  # noqa: E402
main()
