"""Put src/ on sys.path for scripts run directly.

Tests get this from pytest's `pythonpath` setting; scripts import this module
first. The project is an application rather than an installed package, so there
is no third place this could come from.
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
