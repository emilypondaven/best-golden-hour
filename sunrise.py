"""
First Light - sunrise quality forecaster (core pipeline)

Fetches the next 7 days of forecast from Open-Meteo, scores each sunrise
with both a rules baseline and Gemini, caches the result to
scores/YYYY-MM-DD_LAT_LON.json, and prints a ranked table.

Setup:
    pip install -r requirements.txt
    export GEMINI_API_KEY=your-key-here    # free key from aistudio.google.com

Run:
    python sunrise.py                      # uses DEFAULT_PLACE
    python sunrise.py Brighton             # or name anywhere
"""

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

# ---- config -----------------------------------------------------
DEFAULT_PLACE = "London"
MODEL = "gemini-3-flash-preview"
SCORES_DIR = Path(__file__).parent / "scores"
FORECAST_DAYS = 7
# -----------------------------------------------------------------


# ---- 0. where ---------------------------------------------------

def geocode(place: str) -> dict:
    """Place name -> coordinates, plus a tidy label to show back."""
    url = ("https://geocoding-api.open-meteo.com/v1/search"
           f"?name={urllib.parse.quote(place)}&count=1&language=en&format=json")
    with urllib.request.urlopen(url, timeout=15) as r:
        results = json.load(r).get("results")
    if not results:
        raise LookupError(f"Nowhere called '{place}'. Try adding a country.")
    hit = results[0]
    label = ", ".join(x for x in (hit["name"], hit.get("admin1"), hit.get("country")) if x)
    return {"lat": round(hit["latitude"], 2),
            "lon": round(hit["longitude"], 2),
            "place": label}


# ---- 1. data ----------------------------------------------------

def fetch_forecast(lat: float, lon: float) -> list[dict]:
    """One row per day: conditions at the hour nearest sunrise."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&daily=sunrise,sunset"
        "&hourly=cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,"
        "temperature_2m,dew_point_2m,relative_humidity_2m,visibility,"
        "precipitation_probability"
        f"&timezone=auto&forecast_days={FORECAST_DAYS}"
    )
    with urllib.request.urlopen(url, timeout=15) as r:
        d = json.load(r)

    hourly_times = [datetime.fromisoformat(t) for t in d["hourly"]["time"]]

    def at(when: datetime) -> dict:
        """Hourly values at the hour closest to `when`, plus two derived ones."""
        i = min(range(len(hourly_times)),
                key=lambda k: abs(hourly_times[k] - when))
        h = d["hourly"]
        low, mid, high = (h["cloud_cover_low"][i], h["cloud_cover_mid"][i],
                          h["cloud_cover_high"][i])
        total = h["cloud_cover"][i]
        temp, dew = h["temperature_2m"][i], h["dew_point_2m"][i]
        vis = h["visibility"][i]
        return {
            "cloud_total": total,
            "cloud_low": low,
            "cloud_mid": mid,
            "cloud_high": high,
            # Layers sum above the total => they overlap vertically (a lid).
            # Clamped: the layer values and the total come from different
            # derivations and don't always reconcile.
            "stacking": max(0, (low + mid + high) - total) if total is not None else None,
            # Temp minus dewpoint. Under ~2C means fog is likely.
            "fog_risk_c": round(temp - dew, 1) if None not in (temp, dew) else None,
            "humidity": h["relative_humidity_2m"][i],
            "visibility_km": round(vis / 1000) if vis is not None else None,
            "rain_chance": h["precipitation_probability"][i],
        }

    days = []
    for sunrise_iso, sunset_iso in zip(d["daily"]["sunrise"], d["daily"]["sunset"]):
        sunrise = datetime.fromisoformat(sunrise_iso)
        sunset = datetime.fromisoformat(sunset_iso)
        days.append({
            "date": sunrise_iso[:10],
            "weekday": sunrise.strftime("%A"),
            "sunrise": {
                "time": sunrise_iso[11:16],
                **at(sunrise),
            },
            "sunset": {
                "time": sunset_iso[11:16],
                **at(sunset),
            },
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

    if (f.get("fog_risk_c") or 99) < 2:
        score -= 20                     # fog likely: dewpoint depression tiny

    if (f.get("stacking") or 0) > 60:
        score -= 10                     # layers stacked into a lid

    if (f.get("rain_chance") or 0) > 60:
        score -= 10

    return max(0, min(100, score))


# ---- 3. the LLM scorer ------------------------------------------

BRIEF_PATH = Path(__file__).parent / "prompt.md"


def system_brief() -> str:
    """The scorer's instructions, kept in prompt.md so they can be edited
    without touching code. Read fresh each call, so edits apply without a
    restart (--reload only watches .py files)."""
    return BRIEF_PATH.read_text(encoding="utf-8")


def llm_scores(days: list[dict], phase: str, place: str = "", lat: float = 0.0) -> dict:
    """Ask Gemini to score the week for sunrise or sunset. Returns {"_error": ...} on any failure."""
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {"_error": "GEMINI_API_KEY not set - the app never saw a key."}
    if not BRIEF_PATH.exists():
        return {"_error": f"No prompt file at {BRIEF_PATH}"}

    phase = phase.lower()
    if phase not in ("sunrise", "sunset"):
        return {"_error": f"Unknown phase '{phase}'; expected sunrise or sunset."}

    features = [
        {
            "date": day["date"],
            "weekday": day["weekday"],
            "time": day[phase]["time"],
            "cloud_total": day[phase]["cloud_total"],
            "cloud_low": day[phase]["cloud_low"],
            "cloud_mid": day[phase]["cloud_mid"],
            "cloud_high": day[phase]["cloud_high"],
            "stacking": day[phase]["stacking"],
            "fog_risk_c": day[phase]["fog_risk_c"],
            "humidity": day[phase]["humidity"],
            "visibility_km": day[phase]["visibility_km"],
            "rain_chance": day[phase]["rain_chance"],
        }
        for day in days
    ]

    prompt = f"""Location: {place} (latitude {lat}).
Scoring the {len(days)} {phase}s from {days[0]['date']}.
At this latitude and season, consider how fast the sun clears the horizon
and how long any colour is likely to last.

Conditions at {phase}. Cloud values are percent cover.

{json.dumps(features, indent=2)}

Respond with JSON only, no markdown, in exactly this shape:
{{
  "days": [{{"date": "YYYY-MM-DD", "score": 0-100,
             "reason": "max 12 words, name the deciding number"}}],
  "best_date": "YYYY-MM-DD",
  "week_summary": "one sentence, under 20 words"
}}"""

    try:
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config={"system_instruction": system_brief(),
                    "response_mime_type": "application/json",
                    "temperature": 0.3},
        )
        text = (resp.text or "").strip()
        if text.startswith("```"):
            text = text.split("```")[1].removeprefix("json").strip()
        return json.loads(text)
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"}


# ---- 4. assemble ------------------------------------------------

def build_report(place: str) -> dict:
    where = geocode(place)
    days = fetch_forecast(where["lat"], where["lon"])
    for day in days:
        day["sunrise"]["scores"] = {
            "rules_score": rules_score(day["sunrise"]),
            "llm_score": None,
            "reason": "Rules estimate only.",
            "score": None,
        }
        day["sunset"]["scores"] = {
            "rules_score": rules_score(day["sunset"]),
            "llm_score": None,
            "reason": "Rules estimate only.",
            "score": None,
        }

    sunrise_llm = llm_scores(days, "sunrise", where["place"], where["lat"])
    sunset_llm = llm_scores(days, "sunset", where["place"], where["lat"])

    sun_error = sunrise_llm.pop("_error", None)
    set_error = sunset_llm.pop("_error", None)
    if sun_error:
        print(f"!  Sunrise scoring failed - {sun_error}")
    if set_error:
        print(f"!  Sunset scoring failed - {set_error}")

    by_date_sunrise = {d["date"]: d for d in sunrise_llm.get("days", [])}
    by_date_sunset = {d["date"]: d for d in sunset_llm.get("days", [])}

    for day in days:
        sunrise_scored = by_date_sunrise.get(day["date"])
        if sunrise_scored:
            day["sunrise"]["scores"]["llm_score"] = sunrise_scored["score"]
            day["sunrise"]["scores"]["reason"] = sunrise_scored["reason"]
            day["sunrise"]["scores"]["score"] = sunrise_scored["score"]
        else:
            day["sunrise"]["scores"]["score"] = day["sunrise"]["scores"]["rules_score"]

        sunset_scored = by_date_sunset.get(day["date"])
        if sunset_scored:
            day["sunset"]["scores"]["llm_score"] = sunset_scored["score"]
            day["sunset"]["scores"]["reason"] = sunset_scored["reason"]
            day["sunset"]["scores"]["score"] = sunset_scored["score"]
        else:
            day["sunset"]["scores"]["score"] = day["sunset"]["scores"]["rules_score"]

        # keep top-level backward compatibility using sunrise scores by default
        day["rules_score"] = day["sunrise"]["scores"]["rules_score"]
        day["llm_score"] = day["sunrise"]["scores"]["llm_score"]
        day["reason"] = day["sunrise"]["scores"]["reason"]
        day["score"] = day["sunrise"]["scores"]["score"]

    best_sunrise = sunrise_llm.get("best_date") or max(days, key=lambda d: d["sunrise"]["scores"]["score"])["date"]
    best_sunset = sunset_llm.get("best_date") or max(days, key=lambda d: d["sunset"]["scores"]["score"])["date"]

    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "place": where["place"],
        "lat": where["lat"],
        "lon": where["lon"],
        "source": "llm" if by_date_sunrise or by_date_sunset else "rules",
        "error": sun_error or set_error,
        "best_date": best_sunrise,
        "best_sunrise_date": best_sunrise,
        "best_sunset_date": best_sunset,
        "week_summary": sunrise_llm.get("week_summary", "Rules estimate only - no LLM scoring."),
        "sunrise_week_summary": sunrise_llm.get("week_summary", "Rules estimate only - no LLM scoring."),
        "sunset_week_summary": sunset_llm.get("week_summary", "Rules estimate only - no LLM scoring."),
        "days": days,
    }


def slug(place: str) -> str:
    """Filename-safe key: 'Brighton, England, UK' -> 'brighton-england-uk'."""
    keep = "".join(c if c.isalnum() else " " for c in place.lower())
    return "-".join(keep.split())[:60] or "unknown"


def load_or_create(place: str, force: bool = False) -> dict:
    """Score at most once per day per place; the UI only reads this file."""
    SCORES_DIR.mkdir(exist_ok=True)
    path = SCORES_DIR / f"{date.today().isoformat()}_{slug(place)}.json"
    if path.exists() and not force:
        return json.loads(path.read_text())
    report = build_report(place)
    path.write_text(json.dumps(report, indent=2))
    return report


# ---- 5. CLI -----------------------------------------------------

def show(report: dict) -> None:
    print(f"\n  {report['place']}")
    print(f"  {report['week_summary']}\n")
    for d in sorted(report["days"], key=lambda x: x["score"], reverse=True):
        star = "*" if d["date"] == report["best_date"] else " "
        print(f"  {star} {d['weekday']:<10} {d['sunrise']['time']}  {d['score']:>3}  {d['reason']}")
    print()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    place = " ".join(args) or DEFAULT_PLACE
    try:
        show(load_or_create(place, force="--force" in sys.argv))
    except LookupError as e:
        print(f"\n  {e}\n")