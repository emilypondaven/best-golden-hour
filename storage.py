"""Score-report caching. Scoring runs at most once per day per location;
the UI and CLI only ever read the cached file."""

import json
from datetime import date
from pathlib import Path

from scoring import build_report

SCORES_DIR = Path(__file__).parent / "scores"


def load_or_create(lat: float, lon: float, force: bool = False) -> dict:
    """Score at most once per day per location; the UI only reads this file."""
    SCORES_DIR.mkdir(exist_ok=True)
    path = SCORES_DIR / f"{date.today().isoformat()}_{lat:.2f}_{lon:.2f}.json"
    if path.exists() and not force:
        return json.loads(path.read_text())
    report = build_report(lat, lon)
    path.write_text(json.dumps(report, indent=2))
    return report
