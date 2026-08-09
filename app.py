"""
First Light - web server

Run:
    uvicorn app:app --reload
    open http://127.0.0.1:8000
"""

import json
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from sunrise import DEFAULT_PLACE, load_or_create

BASE = Path(__file__).parent
LOG = BASE / "log.jsonl"

app = FastAPI(title="First Light")


@app.get("/api/forecast")
def forecast(place: str = DEFAULT_PLACE):
    """Scored week for a named place. Cached to one scoring run per day."""
    place = place.strip()
    if not place:
        raise HTTPException(400, "Type somewhere to look up.")
    try:
        return load_or_create(place)
    except LookupError as e:
        raise HTTPException(404, str(e))          # no such place
    except Exception as e:
        raise HTTPException(502, f"Could not build a forecast: {e}")


class Rating(BaseModel):
    date: str
    rating: int = Field(description="1 for good, -1 for not")
    place: str = DEFAULT_PLACE


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

    report = load_or_create(r.place)
    day = next((d for d in report["days"] if d["date"] == r.date), None)
    if day is None:
        raise HTTPException(404, f"no forecast on file for {r.date}")

    entry = {
        "date": r.date,
        "rating": r.rating,
        "place": report["place"],
        "lat": report["lat"],
        "lon": report["lon"],
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


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")