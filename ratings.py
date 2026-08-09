"""Append-only JSONL log of rated mornings - the training set.

Each line pairs a user's verdict with the features and score predicted
for that morning. append() and read_all() are the only two operations;
the file format and locking are private to this module.
"""

import json
import threading
from pathlib import Path

LOG_PATH = Path(__file__).parent / "log.jsonl"

# Protects in-process writes. Doesn't solve multi-process contention
# (gunicorn/uvicorn with multiple workers) but avoids interleaved lines
# in the common single-process dev server.
_LOCK = threading.Lock()


def append(entry: dict) -> None:
    with _LOCK:
        with LOG_PATH.open("a") as f:
            f.write(json.dumps(entry) + "\n")


def read_all() -> list[dict]:
    if not LOG_PATH.exists():
        return []
    return [json.loads(line) for line in LOG_PATH.read_text().splitlines() if line.strip()]
