"""
First Light - web server

Run:
    uvicorn app:app --reload
    open http://127.0.0.1:8000
"""

from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from geocode import place_name, search_place
from ratings import append as append_rating, read_all as read_ratings
from storage import load_or_create

BASE = Path(__file__).parent

app = FastAPI(title="First Light")


def check(lat: float, lon: float) -> None:
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(400, "Those coordinates aren't on Earth.")


@app.get("/api/forecast")
def forecast(lat: float, lon: float, phase: str | None = None):
    """Scored week for a location. Coordinates are required - they come from
    the browser. Cached to one scoring run per day."""
    check(lat, lon)
    if phase is not None and phase.lower() not in ("sunrise", "sunset"):
        raise HTTPException(400, "phase must be sunrise or sunset")
    try:
        report = load_or_create(lat, lon)
        if phase is None:
            return report
        phase = phase.lower()
        return {
            **report,
            "phase": phase,
            "best_date": report[f"best_{phase}_date"],
            "week_summary": report[f"{phase}_week_summary"],
        }
    except Exception as e:
        raise HTTPException(502, f"Could not build a forecast: {e}")


@app.get("/api/search")
def search(q: str):
    """Name -> coordinates + label. Free geocoder, no Google key involved."""
    q = q.strip()
    if not q:
        raise HTTPException(400, "Type somewhere to look up.")
    try:
        return search_place(q)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(502, f"Lookup failed: {e}")


@app.get("/api/whereami")
def whereami(lat: float, lon: float):
    """Name a set of coordinates. Called once after GPS, then cached in the
    browser - this is the only thing that spends the Google key."""
    check(lat, lon)
    return {"lat": round(lat, 2), "lon": round(lon, 2),
            "place": place_name(round(lat, 2), round(lon, 2))}


class Rating(BaseModel):
    date: date
    rating: int = Field(description="1 for good, -1 for not")
    lat: float
    lon: float
    phase: str = "sunrise"


@app.post("/api/rate")
def rate(r: Rating):
    """
    Log what the sky actually did. Each line pairs your verdict with the
    features and score we predicted from - this is the training set.
    """
    if r.rating not in (1, -1):
        raise HTTPException(400, "rating must be 1 or -1")
    if r.date > date.today():
        raise HTTPException(400, "that morning hasn't happened yet")
    phase = r.phase.lower()
    if phase not in ("sunrise", "sunset"):
        raise HTTPException(400, "phase must be sunrise or sunset")
    check(r.lat, r.lon)

    report = load_or_create(r.lat, r.lon)
    day = next((d for d in report["days"] if d["date"] == r.date.isoformat()), None)
    if day is None:
        raise HTTPException(404, f"no forecast on file for {r.date}")

    block = day[phase]
    entry = {
        "date": r.date.isoformat(),
        "phase": phase,
        "rating": r.rating,
        "lat": report["lat"],
        "lon": report["lon"],
        "predicted_score": block["scores"]["score"],
        "rules_score": block["scores"]["rules_score"],
        "llm_score": block["scores"]["llm_score"],
        "reason": block["scores"]["reason"],
        "features": {k: block[k] for k in ("cloud_total", "cloud_low", "cloud_mid",
                                           "cloud_high", "stacking", "fog_risk_c",
                                           "humidity", "visibility_km", "rain_chance")},
        "logged_at": date.today().isoformat(),
    }
    append_rating(entry)
    return {"ok": True, "logged": entry["date"]}


@app.get("/api/ratings")
def ratings():
    """Everything logged so far, so the UI can show which days you've rated."""
    return read_ratings()


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")