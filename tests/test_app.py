"""/api/forecast and /api/rate, exercised with a faked load_or_create so
no real scoring, network, or disk I/O happens."""

from datetime import date, timedelta

import app as app_module


def _fake_report(lat=51.42, lon=-0.27):
    day = {
        "date": "2026-08-01",
        "weekday": "Saturday",
        "sunrise": {
            "time": "05:45",
            "cloud_total": 50, "cloud_low": 10, "cloud_mid": 40, "cloud_high": 20,
            "stacking": 5, "fog_risk_c": 5, "humidity": 60, "visibility_km": 20,
            "rain_chance": 5,
            "scores": {"rules_score": 80, "llm_score": 85, "reason": "clear high cloud", "score": 85},
        },
        "sunset": {
            "time": "20:30",
            "cloud_total": 30, "cloud_low": 5, "cloud_mid": 20, "cloud_high": 10,
            "stacking": 0, "fog_risk_c": 8, "humidity": 50, "visibility_km": 25,
            "rain_chance": 0,
            "scores": {"rules_score": 60, "llm_score": 65, "reason": "thin cloud", "score": 65},
        },
        "rules_score": 80, "llm_score": 85, "reason": "clear high cloud", "score": 85,
    }
    return {
        "generated": "2026-08-01T06:00:00",
        "lat": lat, "lon": lon,
        "source": "llm", "provider": "fake", "error": None,
        "best_date": "2026-08-01",
        "best_sunrise_date": "2026-08-01",
        "best_sunset_date": "2026-08-01",
        "week_summary": "sunrise summary",
        "sunrise_week_summary": "sunrise summary",
        "sunset_week_summary": "sunset summary",
        "days": [day],
    }


# ---- /api/forecast ------------------------------------------------

def test_forecast_returns_full_report_by_default(client, monkeypatch):
    monkeypatch.setattr(app_module, "load_or_create", lambda lat, lon: _fake_report())

    r = client.get("/api/forecast", params={"lat": 51.5, "lon": -0.1})

    assert r.status_code == 200
    assert r.json() == _fake_report()


def test_forecast_reshapes_response_for_phase(client, monkeypatch):
    monkeypatch.setattr(app_module, "load_or_create", lambda lat, lon: _fake_report())

    r = client.get("/api/forecast", params={"lat": 51.5, "lon": -0.1, "phase": "sunset"})

    body = r.json()
    assert r.status_code == 200
    assert body["phase"] == "sunset"
    assert body["best_date"] == _fake_report()["best_sunset_date"]
    assert body["week_summary"] == _fake_report()["sunset_week_summary"]


def test_forecast_rejects_invalid_phase(client, monkeypatch):
    monkeypatch.setattr(app_module, "load_or_create", lambda lat, lon: _fake_report())

    r = client.get("/api/forecast", params={"lat": 51.5, "lon": -0.1, "phase": "noon"})

    assert r.status_code == 400


def test_forecast_rejects_bad_coordinates(client):
    r = client.get("/api/forecast", params={"lat": 999, "lon": -0.1})

    assert r.status_code == 400


def test_forecast_wraps_scoring_failures_as_502(client, monkeypatch):
    def boom(lat, lon):
        raise RuntimeError("upstream is down")

    monkeypatch.setattr(app_module, "load_or_create", boom)

    r = client.get("/api/forecast", params={"lat": 51.5, "lon": -0.1})

    assert r.status_code == 502


# ---- /api/rate ------------------------------------------------

def test_rate_logs_entry_and_returns_ok(client, monkeypatch):
    monkeypatch.setattr(app_module, "load_or_create", lambda lat, lon: _fake_report())
    logged = []
    monkeypatch.setattr(app_module, "append_rating", logged.append)

    r = client.post("/api/rate", json={
        "date": "2026-08-01", "rating": 1, "lat": 51.5, "lon": -0.1, "phase": "sunrise",
    })

    assert r.status_code == 200
    assert r.json() == {"ok": True, "logged": "2026-08-01"}
    assert len(logged) == 1
    entry = logged[0]
    assert entry["phase"] == "sunrise"
    assert entry["rating"] == 1
    assert entry["predicted_score"] == 85
    assert entry["rules_score"] == 80
    assert entry["llm_score"] == 85
    assert entry["features"]["cloud_mid"] == 40


def test_rate_rejects_bad_rating_value(client, monkeypatch):
    monkeypatch.setattr(app_module, "load_or_create", lambda lat, lon: _fake_report())

    r = client.post("/api/rate", json={
        "date": "2026-08-01", "rating": 5, "lat": 51.5, "lon": -0.1, "phase": "sunrise",
    })

    assert r.status_code == 400


def test_rate_rejects_future_date(client, monkeypatch):
    monkeypatch.setattr(app_module, "load_or_create", lambda lat, lon: _fake_report())
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    r = client.post("/api/rate", json={
        "date": tomorrow, "rating": 1, "lat": 51.5, "lon": -0.1, "phase": "sunrise",
    })

    assert r.status_code == 400


def test_rate_404s_when_date_not_on_file(client, monkeypatch):
    monkeypatch.setattr(app_module, "load_or_create", lambda lat, lon: _fake_report())

    r = client.post("/api/rate", json={
        "date": "2026-07-01", "rating": 1, "lat": 51.5, "lon": -0.1, "phase": "sunrise",
    })

    assert r.status_code == 404
