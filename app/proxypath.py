"""Make psionproxy importable from the app, from source or the frozen bundle.

The app and the proxy share a little code -- where files live, what the
computer is called -- so the app needs the proxy's package on its path.
Importing this module is enough.
"""

import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    _base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    _base = Path(__file__).resolve().parent.parent
_p = str(_base / "proxy")
if _p not in sys.path:
    sys.path.insert(0, _p)
