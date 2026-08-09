"""build_report's aggregation logic, exercised with fake scorers and a
faked weather fetch - no network involved."""

import json

import pytest

import scoring

SUNRISE_FEATURES = {
    "time": "05:45", "cloud_total": 50, "cloud_low": 10, "cloud_mid": 40,
    "cloud_high": 20, "stacking": 5, "fog_risk_c": 5, "humidity": 60,
    "visibility_km": 20, "rain_chance": 5,
}
SUNSET_FEATURES = {
    "time": "20:30", "cloud_total": 30, "cloud_low": 5, "cloud_mid": 20,
    "cloud_high": 10, "stacking": 0, "fog_risk_c": 8, "humidity": 50,
    "visibility_km": 25, "rain_chance": 0,
}


def _fake_days():
    return [
        {"date": "2026-08-10", "weekday": "Monday",
         "sunrise": dict(SUNRISE_FEATURES), "sunset": dict(SUNSET_FEATURES)},
        {"date": "2026-08-11", "weekday": "Tuesday",
         "sunrise": dict(SUNRISE_FEATURES), "sunset": dict(SUNSET_FEATURES)},
    ]


@pytest.fixture(autouse=True)
def fake_weather(monkeypatch):
    """build_report always starts with fetch_forecast - fake it so every
    test in this file runs with no network access."""
    monkeypatch.setattr(scoring, "fetch_forecast", lambda lat, lon: _fake_days())


def _scorer(days_payload, best_date="2026-08-10"):
    """A fake Scorer: ignores the prompt, returns a canned response."""
    def call(prompt: str, brief: str) -> str:
        return json.dumps({"days": days_payload, "best_date": best_date,
                            "week_summary": "fake week"})
    return call


def test_build_report_uses_injected_scorer():
    scorer = _scorer([
        {"date": "2026-08-10", "score": 91, "reason": "clear high cloud"},
        {"date": "2026-08-11", "score": 40, "reason": "murky low cloud"},
    ])

    report = scoring.build_report(51.5, -0.1, providers={"fake": scorer})

    assert report["source"] == "llm"
    assert report["provider"] == "fake"
    assert report["error"] is None
    assert report["best_sunrise_date"] == "2026-08-10"
    first_day = report["days"][0]
    assert first_day["sunrise"]["scores"]["llm_score"] == 91
    assert first_day["sunrise"]["scores"]["reason"] == "clear high cloud"
    # rules_score is still computed and kept even though the LLM score won
    assert first_day["sunrise"]["scores"]["rules_score"] == scoring.rules_score(SUNRISE_FEATURES)


def test_build_report_tries_next_provider_on_failure():
    def broken(prompt, brief):
        raise RuntimeError("connection refused")

    good = _scorer([
        {"date": "2026-08-10", "score": 70, "reason": "ok"},
        {"date": "2026-08-11", "score": 70, "reason": "ok"},
    ])

    report = scoring.build_report(51.5, -0.1, providers={"broken": broken, "good": good})

    assert report["source"] == "llm"
    assert report["provider"] == "good"


def test_build_report_falls_back_to_rules_when_every_provider_fails():
    def broken(prompt, brief):
        raise RuntimeError("connection refused")

    report = scoring.build_report(51.5, -0.1, providers={"broken": broken})

    assert report["source"] == "rules"
    assert report["provider"] is None
    assert "broken" in report["error"]
    day = report["days"][0]
    assert day["sunrise"]["scores"]["llm_score"] is None
    assert day["sunrise"]["scores"]["score"] == day["sunrise"]["scores"]["rules_score"]


def test_rules_score_reads_thresholds_from_config():
    neutral_rules = {
        "base_score": 42,
        "upper_cloud": {"low": 0, "high": 100, "in_range_bonus": 0,
                         "overcast_threshold": 999, "overcast_penalty": 0, "clear_penalty": 0},
        "low_cloud": {"blocked_threshold": 999, "blocked_penalty": 0,
                      "murky_threshold": 999, "murky_penalty": 0},
        "fog": {"threshold_c": -999, "penalty": 0},
        "stacking": {"threshold": 999, "penalty": 0},
        "rain": {"threshold": 999, "penalty": 0},
    }
    assert scoring.rules_score(SUNRISE_FEATURES, rules=neutral_rules) == 42
