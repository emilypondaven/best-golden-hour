"""
First Light - sunrise quality forecaster (core pipeline)

Fetches the next 7 days of forecast from Open-Meteo, scores each sunrise
with both a rules baseline and Gemini, caches the result to
scores/YYYY-MM-DD_LAT_LON.json, and prints a ranked table.

Setup:
    pip install -r requirements.txt
    export GEMINI_API_KEY=your-key-here    # free key from aistudio.google.com

Run:
    python sunrise.py 51.42 -0.27          # coordinates are required
"""

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

# ---- config -----------------------------------------------------
MODEL = "gemini-3-flash-preview"
SCORES_DIR = Path(__file__).parent / "scores"
FORECAST_DAYS = 7
# -----------------------------------------------------------------


# ---- 0. naming the place (display only) -------------------------

# Which service your key is for. The rest of the app never reads the label,
# so if this fails the app carries on and the footer shows coordinates.
GEOCODER = "google" 

_PROVIDERS = {
    "opencage": (
        "https://api.opencagedata.com/geocode/v1/json"
        "?q={lat}+{lon}&key={key}&no_annotations=1&limit=1",
        lambda d: (d.get("results") or [{}])[0].get("formatted"),
    ),
    "locationiq": (
        "https://us1.locationiq.com/v1/reverse"
        "?key={key}&lat={lat}&lon={lon}&format=json",
        lambda d: d.get("display_name"),
    ),
    "google": (
        "https://maps.googleapis.com/maps/api/geocode/json"
        "?latlng={lat},{lon}&key={key}&result_type=postal_town|locality"
        "&language=en",
        # Built from address_components, not formatted_address - Google's docs
        # say not to parse that string programmatically.
        lambda d: _google_label(d),
    ),
}


def _google_label(d: dict) -> str | None:
    """Pull a short label out of a Google Geocoding response."""
    if d.get("status") != "OK" or not d.get("results"):
        print(f"!  Google geocode: {d.get('status')} "
              f"{d.get('error_message', '')}".strip())
        return None
    parts = []
    for want in ("postal_town", "locality", "administrative_area_level_1"):
        for c in d["results"][0]["address_components"]:
            if want in c["types"] and c["long_name"] not in parts:
                parts.append(c["long_name"])
                break
    return ", ".join(parts) or None


def place_name(lat: float, lon: float) -> str | None:
    """Coordinates -> a human label, purely for display. Returns None on any
    failure: a missing name is cosmetic, so it must never break a forecast."""
    key = os.environ.get("GEOCODE_API_KEY")
    if not key or GEOCODER not in _PROVIDERS:
        return None
    template, extract = _PROVIDERS[GEOCODER]
    url = template.format(lat=lat, lon=lon, key=urllib.parse.quote(key))
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            label = extract(json.load(r))
        # Trim the long tails these services return: keep the first three parts.
        return ", ".join(p.strip() for p in (label or "").split(",")[:3]) or None
    except Exception as e:
        print(f"!  Place lookup failed ({type(e).__name__}: {e}) - showing coordinates.")
        return None


def search_place(q: str) -> dict:
    """
    Name -> coordinates + label, via Open-Meteo's own geocoder. Free and
    keyless, so searching never touches the paid Google key. The label comes
    back with the result, so a search needs no reverse lookup either.
    """
    url = ("https://geocoding-api.open-meteo.com/v1/search"
           f"?name={urllib.parse.quote(q)}&count=1&language=en&format=json")
    with urllib.request.urlopen(url, timeout=10) as r:
        results = json.load(r).get("results")
    if not results:
        raise LookupError(f"Nowhere called '{q}'. Try adding a country.")
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


def llm_scores(days: list[dict], phase: str, lat: float = 0.0) -> dict:
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

    prompt = f"""Latitude {lat}.
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

def build_report(lat: float, lon: float) -> dict:
    lat, lon = round(lat, 2), round(lon, 2)
    days = fetch_forecast(lat, lon)
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

    sunrise_llm = llm_scores(days, "sunrise", lat)
    sunset_llm = llm_scores(days, "sunset", lat)

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
        "lat": lat,
        "lon": lon,
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


def load_or_create(lat: float, lon: float, force: bool = False) -> dict:
    """Score at most once per day per location; the UI only reads this file."""
    SCORES_DIR.mkdir(exist_ok=True)
    path = SCORES_DIR / f"{date.today().isoformat()}_{lat:.2f}_{lon:.2f}.json"
    if path.exists() and not force:
        return json.loads(path.read_text())
    report = build_report(lat, lon)
    path.write_text(json.dumps(report, indent=2))
    return report


# ---- 5. CLI -----------------------------------------------------

def show(report: dict) -> None:
    where = f"{report['lat']}, {report['lon']}"
    print(f"\n  {where}")
    print(f"  {report['week_summary']}\n")
    for d in sorted(report["days"], key=lambda x: x["score"], reverse=True):
        star = "*" if d["date"] == report["best_date"] else " "
        print(f"  {star} {d['weekday']:<10} {d['sunrise']['time']}  {d['score']:>3}  {d['reason']}")
    print()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        sys.exit("Usage: python sunrise.py LAT LON   e.g. python sunrise.py 51.42 -0.27")
    show(load_or_create(float(args[0]), float(args[1]), force="--force" in sys.argv))