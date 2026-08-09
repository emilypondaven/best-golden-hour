"""Open-Meteo fetch and per-day feature derivation."""

import json
import urllib.request
from datetime import datetime

FORECAST_DAYS = 7


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
