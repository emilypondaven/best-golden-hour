"""Coordinates <-> place names.

place_name() reverse-geocodes via a paid key (Google by default) and is
purely cosmetic - it must never break a forecast, so it swallows its own
failures. search_place() is the free, keyless forward search used by the
UI's location box.
"""

import json
import os
import urllib.parse
import urllib.request

from dotenv import load_dotenv

load_dotenv()

# Which service GEOCODE_API_KEY is for. The rest of the app never reads the
# label, so if this fails the app carries on and the footer shows coordinates.
GEOCODER = "google"          # opencage | locationiq | google

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
