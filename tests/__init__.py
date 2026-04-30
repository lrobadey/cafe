"""Test package setup for direct unittest discovery from the repo root."""

import sys
from pathlib import Path

CAFE_SIM_PATH = Path(__file__).resolve().parent.parent / "cafe_sim"
if str(CAFE_SIM_PATH) not in sys.path:
    sys.path.insert(0, str(CAFE_SIM_PATH))
