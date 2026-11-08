import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for relative in ("shared", "station-app", "central-server"):
    entry = str(ROOT / relative)
    if entry not in sys.path:
        sys.path.insert(0, entry)
