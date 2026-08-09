"""
First Light - sunrise quality forecaster (core pipeline)

Fetches the next 7 days of forecast from Open-Meteo, scores each sunrise
with both a rules baseline and Gemini, caches the result to
scores/YYYY-MM-DD_LAT_LON.json, and prints a ranked table.

Setup:
    pip install -r requirements.txt
    export GEMINI_API_KEY=your-key-here    # free key from aistudio.google.com

Run:
    python sunrise.py                      # uses DEFAULT_LAT / DEFAULT_LON
    python sunrise.py 51.47 -0.36          # or pass a location
"""

import json
import os
import sys
import urllib.request
from datetime import date, datetime
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

# ---- config -----------------------------------------------------
DEFAULT_LAT, DEFAULT_LON = 51.4746, -0.3680   # Hounslow - change to your spot
MODEL = "gemini-3-flash"
SCORES_DIR = Path(__file__).parent / "scores"
FORECAST_DAYS = 7
# -----------------------------------------------------------------


# ---- 1. data ----------------------------------------------------

def fetch_forecast(lat: float, lon: float) -> list[dict]:
    """One row per day: conditions at the hour nearest sunrise."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&daily=sunrise,sunset"
        "&hourly=cloud_cover_low,cloud_cover_mid,cloud_cover_high,"
        "relative_humidity_2m,visibility,precipitation_probability"
        f"&timezone=auto&forecast_days={FORECAST_DAYS}"
    )
    with urllib.request.urlopen(url, timeout=15) as r:
        d = json.load(r)

    hourly_times = [datetime.fromisoformat(t) for t in d["hourly"]["time"]]

    def at(when: datetime) -> dict:
        """Hourly values at the hour closest to `when`."""
        i = min(range(len(hourly_times)),
                key=lambda k: abs(hourly_times[k] - when))
        h = d["hourly"]
        return {
            "cloud_low": h["cloud_cover_low"][i],
            "cloud_mid": h["cloud_cover_mid"][i],
            "cloud_high": h["cloud_cover_high"][i],
            "humidity": h["relative_humidity_2m"][i],
            "visibility_km": round((h["visibility"][i] or 0) / 1000),
            "rain_chance": h["precipitation_probability"][i],
        }

    days = []
    for sunrise_iso, sunset_iso in zip(d["daily"]["sunrise"], d["daily"]["sunset"]):
        sunrise = datetime.fromisoformat(sunrise_iso)
        sunset = datetime.fromisoformat(sunset_iso)
        days.append({
            "date": sunrise_iso[:10],
            "weekday": sunrise.strftime("%A"),
            "sunrise": sunrise_iso[11:16],
            "sunset": sunset_iso[11:16],
            **at(sunrise),
            "evening": at(sunset),
        })
    return days


# ---- 2. rules baseline -----------------------------------------

def rules_score(f: dict) -> int:
    """
    Hand-rolled baseline. Saved in the file but never shown in the UI -
    it's a silent control so LLM disagreements can be reviewed later.
    """
    score = 50
    upper = max(f["cloud_mid"], f["cloud_high"])

    if 20 <= upper <= 70:
        score += 30                     # cloud up high to catch the light
    elif upper > 85:
        score -= 15                     # grey lid, no light gets through
    else:
        score -= 5                      # clear sky: pleasant but plain

    if f["cloud_low"] > 70:
        score -= 40                     # the horizon is bricked up
    elif f["cloud_low"] > 40:
        score -= 25                     # murk sitting on the horizon

    if f["humidity"] > 95 and f["visibility_km"] < 5:
        score -= 20                     # fog risk

    if (f.get("rain_chance") or 0) > 60:
        score -= 10

    return max(0, min(100, score))


# ---- 3. the LLM scorer ------------------------------------------

SYSTEM_BRIEF = """You are an expert sunrise-quality forecaster.

What makes a vivid sunrise:
- Mid or high altitude cloud, roughly 20-70% cover, to catch pink and gold
  light from below. This is the single most important factor.
- A clear path near the horizon. Low cloud above about 40% usually means
  the light never reaches the underside of the higher cloud: grey murk.
- A fully clear sky is pleasant but plain - a clean gradient, no drama.
- Very high humidity with low visibility suggests fog, which can either
  ruin it or make it beautiful; treat it as a risk, and say so.
- Total overcast at every level is a write-off.

Score each day 0-100. Be decisive and use the full range: most weeks
contain at least one dud in the teens. Do not cluster everything at 50."""


def llm_scores(days: list[dict]) -> dict:
    """Ask Gemini to score the week. Returns {} on any failure."""
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("!  GEMINI_API_KEY not set - using rules scores only.")
        return {}

    features = [
        {k: day[k] for k in ("date", "weekday", "sunrise", "cloud_low",
                             "cloud_mid", "cloud_high", "humidity",
                             "visibility_km", "rain_chance")}
        for day in days
    ]

    prompt = f"""{SYSTEM_BRIEF}

Here is the forecast at sunrise for the next {len(days)} days.
Cloud cover values are percentages by altitude band.

{json.dumps(features, indent=2)}

Respond with JSON only, no markdown, in exactly this shape:
{{
  "days": [{{"date": "YYYY-MM-DD", "score": 0-100,
             "reason": "max 12 words, concrete, no hedging"}}],
  "best_date": "YYYY-MM-DD",
  "week_summary": "one sentence, under 20 words"
}}"""

    try:
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config={"response_mime_type": "application/json",
                    "temperature": 0.3},
        )
        text = (resp.text or "").strip()
        if text.startswith("```"):
            text = text.split("```")[1].removeprefix("json").strip()
        return json.loads(text)
    except Exception as e:
        print(f"!  Scoring failed ({type(e).__name__}: {e}) - falling back to rules.")
        return {}


# ---- 4. assemble ------------------------------------------------

def build_report(lat: float, lon: float) -> dict:
    days = fetch_forecast(lat, lon)
    for day in days:
        day["rules_score"] = rules_score(day)

    llm = llm_scores(days)
    by_date = {d["date"]: d for d in llm.get("days", [])}

    for day in days:
        scored = by_date.get(day["date"])
        day["llm_score"] = scored["score"] if scored else None
        day["reason"] = scored["reason"] if scored else "Rules estimate only."
        day["score"] = day["llm_score"] if day["llm_score"] is not None else day["rules_score"]

    best = llm.get("best_date") or max(days, key=lambda d: d["score"])["date"]

    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "lat": lat,
        "lon": lon,
        "source": "llm" if llm else "rules",
        "best_date": best,
        "week_summary": llm.get("week_summary", "Rules estimate only - no LLM scoring."),
        "days": days,
    }


def cache_path(lat: float, lon: float) -> Path:
    return SCORES_DIR / f"{date.today().isoformat()}_{lat:.2f}_{lon:.2f}.json"


def load_or_create(lat: float, lon: float, force: bool = False) -> dict:
    """Score at most once per day per location; the UI only reads this file."""
    SCORES_DIR.mkdir(exist_ok=True)
    path = cache_path(lat, lon)
    if path.exists() and not force:
        return json.loads(path.read_text())
    report = build_report(lat, lon)
    path.write_text(json.dumps(report, indent=2))
    return report


# ---- 5. CLI -----------------------------------------------------

def show(report: dict) -> None:
    print(f"\n  {report['week_summary']}\n")
    for d in sorted(report["days"], key=lambda x: x["score"], reverse=True):
        star = "*" if d["date"] == report["best_date"] else " "
        print(f"  {star} {d['weekday']:<10} {d['sunrise']}  {d['score']:>3}  {d['reason']}")
    print()


if __name__ == "__main__":
    lat, lon = DEFAULT_LAT, DEFAULT_LON
    if len(sys.argv) == 3:
        lat, lon = float(sys.argv[1]), float(sys.argv[2])
    show(load_or_create(lat, lon, force="--force" in sys.argv))
