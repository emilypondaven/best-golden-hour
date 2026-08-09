"""
First Light - web server

Run:
    uvicorn app:app --reload
    open http://127.0.0.1:8000
"""

import json
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from sunrise import DEFAULT_LAT, DEFAULT_LON, load_or_create

BASE = Path(__file__).parent
LOG = BASE / "log.jsonl"

app = FastAPI(title="First Light")


@app.get("/api/forecast")
def forecast(lat: float = DEFAULT_LAT, lon: float = DEFAULT_LON):
    """Scored week for a location. Cached to one scoring run per day."""
    try:
        return load_or_create(round(lat, 2), round(lon, 2))
    except Exception as e:
        raise HTTPException(502, f"Could not build a forecast: {e}")


class Rating(BaseModel):
    date: str
    rating: int = Field(description="1 for good, -1 for not")
    lat: float = DEFAULT_LAT
    lon: float = DEFAULT_LON


@app.post("/api/rate")
def rate(r: Rating):
    """
    Log what the sky actually did. Each line pairs your verdict with the
    features and score we predicted from - this is the training set.
    """
    if r.rating not in (1, -1):
        raise HTTPException(400, "rating must be 1 or -1")
    if r.date > date.today().isoformat():
        raise HTTPException(400, "that morning hasn't happened yet")

    report = load_or_create(round(r.lat, 2), round(r.lon, 2))
    day = next((d for d in report["days"] if d["date"] == r.date), None)
    if day is None:
        raise HTTPException(404, f"no forecast on file for {r.date}")

    entry = {
        "date": r.date,
        "rating": r.rating,
        "lat": r.lat,
        "lon": r.lon,
        "predicted_score": day["score"],
        "rules_score": day["rules_score"],
        "llm_score": day["llm_score"],
        "reason": day["reason"],
        "features": {k: day[k] for k in ("cloud_low", "cloud_mid", "cloud_high",
                                         "humidity", "visibility_km", "rain_chance")},
        "logged_at": date.today().isoformat(),
    }
    with LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    return {"ok": True, "logged": entry["date"]}


@app.get("/api/ratings")
def ratings():
    """Everything logged so far, so the UI can show which days you've rated."""
    if not LOG.exists():
        return []
    return [json.loads(line) for line in LOG.read_text().splitlines() if line.strip()]


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")