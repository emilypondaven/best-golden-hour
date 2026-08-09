"""Turning a week of weather into a scored report.

Two scorers run over the same features: a hand-rolled rules baseline
(deterministic, always available) and an LLM (tried provider by provider,
falls back to rules-only if every provider fails). build_report() fetches
the week and merges both into the shape the UI and CLI read.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Protocol

from dotenv import load_dotenv

from weather import fetch_forecast

load_dotenv()

# ---- config -----------------------------------------------------
GEMINI_MODEL = "gemini-3-flash-preview"   # check aistudio.google.com for current names
GROQ_MODEL = "openai/gpt-oss-120b"        # check console.groq.com/docs/models
# -----------------------------------------------------------------

PHASES = ("sunrise", "sunset")


# ---- rules baseline -----------------------------------------

RULES_PATH = Path(__file__).parent / "rules.json"


def _load_rules() -> dict:
    """Rules-baseline thresholds, kept in rules.json so they can be tuned
    without touching code. Read fresh each call, same reasoning as
    system_brief(): edits apply without a restart (--reload only watches
    .py files)."""
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


def rules_score(f: dict, rules: dict | None = None) -> int:
    """
    Hand-rolled baseline. Saved in the file but never shown in the UI -
    it's a silent control so LLM disagreements can be reviewed later.

    upper_cloud rewards mid/high cloud in the sweet spot (it catches the
    light) and penalises a solid overcast lid. low_cloud penalises cloud
    sitting on the horizon itself, which blocks the light outright. fog
    and stacking are secondary penalties; rain is a mild one.
    """
    rules = _load_rules() if rules is None else rules
    score = rules["base_score"]

    # Defensive: weather values can be None. Coerce to sensible defaults
    cloud_mid = f.get("cloud_mid") if f.get("cloud_mid") is not None else 0
    cloud_high = f.get("cloud_high") if f.get("cloud_high") is not None else 0
    cloud_low = f.get("cloud_low") if f.get("cloud_low") is not None else 0
    stacking = f.get("stacking") if f.get("stacking") is not None else 0
    rain_chance = f.get("rain_chance") if f.get("rain_chance") is not None else 0

    upper = max(cloud_mid, cloud_high)
    uc = rules["upper_cloud"]
    if uc["low"] <= upper <= uc["high"]:
        score += uc["in_range_bonus"]
    elif upper > uc["overcast_threshold"]:
        score += uc["overcast_penalty"]
    else:
        score += uc["clear_penalty"]

    lc = rules["low_cloud"]
    if cloud_low > lc["blocked_threshold"]:
        score += lc["blocked_penalty"]
    elif cloud_low > lc["murky_threshold"]:
        score += lc["murky_penalty"]

    # fog_risk_c is temp-dewpoint; only penalise when the value exists
    fog_risk = f.get("fog_risk_c")
    fog = rules["fog"]
    if fog_risk is not None and fog_risk < fog["threshold_c"]:
        score += fog["penalty"]

    st = rules["stacking"]
    if stacking > st["threshold"]:
        score += st["penalty"]

    rn = rules["rain"]
    if rain_chance > rn["threshold"]:
        score += rn["penalty"]

    return max(0, min(100, score))


# ---- the LLM scorer ------------------------------------------

class Scorer(Protocol):
    """What a provider caller must look like: raw prompt + brief in,
    raw response text out. Lets build_report/llm_scores take a fake
    in tests without touching a network."""
    def __call__(self, prompt: str, brief: str) -> str: ...


BRIEF_PATH = Path(__file__).parent / "prompt.md"


def system_brief() -> str:
    """The scorer's instructions, kept in prompt.md so they can be edited
    without touching code. Read fresh each call, so edits apply without a
    restart (--reload only watches .py files)."""
    return BRIEF_PATH.read_text(encoding="utf-8")


def _call_gemini(prompt: str, brief: str) -> str:
    """Google. Note: the free tier is not available in the EU, UK or
    Switzerland - expect this one to fail from London."""
    from google import genai

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set")
    resp = genai.Client(api_key=key).models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config={"system_instruction": brief,
                "response_mime_type": "application/json",
                "temperature": 0.3},
    )
    return resp.text or ""


def _call_groq(prompt: str, brief: str) -> str:
    """Groq, through the OpenAI SDK - they speak the same protocol. 1,000
    requests a day free, which is what makes prompt tuning practical."""
    from openai import OpenAI

    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY not set")
    client = OpenAI(api_key=key, base_url="https://api.groq.com/openai/v1")
    resp = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "system", "content": brief},
                  {"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.3,
    )
    return resp.choices[0].message.content or ""


# Tried in order until one answers, so a dead key or a retired model name
# falls through instead of dropping you to rules scores. Add or reorder
# freely - a name and its caller live in the same entry, so they can't
# drift out of sync the way two parallel structures could.
PROVIDERS: dict[str, Scorer] = {"gemini": _call_gemini, "groq": _call_groq}


def llm_scores(days: list[dict], phase: str, lat: float = 0.0,
                providers: dict[str, Scorer] | None = None) -> dict:
    """
    Score a week of sunrises or sunsets. Tries each provider in `providers`
    (default: all configured callers) until one answers, so a dead key or a
    retired model name falls through instead of dropping you to rules scores.

    Returns the parsed JSON plus "_provider", or {"_error": ...} if every
    provider failed.
    """
    if not BRIEF_PATH.exists():
        return {"_error": f"No prompt file at {BRIEF_PATH}"}

    providers = PROVIDERS if providers is None else providers

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

    brief = system_brief()
    failures = []
    for name, caller in providers.items():
        try:
            text = (caller(prompt, brief) or "").strip()
            # Some providers return fenced codeblocks or extra text. Try
            # a tolerant parse: plain json first, then extract braces.
            try:
                out = json.loads(text)
            except Exception:
                if text.startswith("```"):
                    # try to get content inside fences
                    parts = text.split("```")
                    if len(parts) >= 2:
                        candidate = parts[1]
                    else:
                        candidate = text
                else:
                    candidate = text
                # Fallback: trim to first { ... } block if possible
                start = candidate.find("{")
                end = candidate.rfind("}")
                if start != -1 and end != -1 and end > start:
                    try:
                        out = json.loads(candidate[start:end+1])
                    except Exception:
                        raise
                else:
                    raise
            if not out.get("days"):
                raise ValueError("no days in response")
            out["_provider"] = name
            if failures:
                print(f"!  {name} scored the {phase}s after: {'; '.join(failures)}")
            return out
        except Exception as e:
            failures.append(f"{name}: {type(e).__name__}: {e}")

    return {"_error": " | ".join(failures)}


# ---- assemble ------------------------------------------------

def build_report(lat: float, lon: float, providers: dict[str, Scorer] | None = None) -> dict:
    lat, lon = round(lat, 2), round(lon, 2)
    days = fetch_forecast(lat, lon)
    for day in days:
        for phase in PHASES:
            day[phase]["scores"] = {
                "rules_score": rules_score(day[phase]),
                "llm_score": None,
                "reason": "Rules estimate only.",
                "score": None,
            }

    llm = {phase: llm_scores(days, phase, lat, providers) for phase in PHASES}
    error = {phase: llm[phase].pop("_error", None) for phase in PHASES}
    provider_by_phase = {phase: llm[phase].pop("_provider", None) for phase in PHASES}
    for phase in PHASES:
        if error[phase]:
            print(f"!  {phase.capitalize()} scoring failed - {error[phase]}")

    by_date = {phase: {d["date"]: d for d in llm[phase].get("days", [])} for phase in PHASES}

    for day in days:
        for phase in PHASES:
            scores = day[phase]["scores"]
            scored = by_date[phase].get(day["date"])
            if scored:
                scores["llm_score"] = scored["score"]
                scores["reason"] = scored["reason"]
                scores["score"] = scored["score"]
            else:
                scores["score"] = scores["rules_score"]

        # keep top-level backward compatibility using sunrise scores by default
        day["rules_score"] = day["sunrise"]["scores"]["rules_score"]
        day["llm_score"] = day["sunrise"]["scores"]["llm_score"]
        day["reason"] = day["sunrise"]["scores"]["reason"]
        day["score"] = day["sunrise"]["scores"]["score"]

    best_date = {
        phase: llm[phase].get("best_date")
        or max(days, key=lambda d: d[phase]["scores"]["score"])["date"]
        for phase in PHASES
    }
    week_summary = {
        phase: llm[phase].get("week_summary", "Rules estimate only - no LLM scoring.")
        for phase in PHASES
    }

    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "lat": lat,
        "lon": lon,
        "source": "llm" if by_date["sunrise"] or by_date["sunset"] else "rules",
        "provider": provider_by_phase["sunrise"] or provider_by_phase["sunset"],
        "error": error["sunrise"] or error["sunset"],
        "best_date": best_date["sunrise"],
        "best_sunrise_date": best_date["sunrise"],
        "best_sunset_date": best_date["sunset"],
        "week_summary": week_summary["sunrise"],
        "sunrise_week_summary": week_summary["sunrise"],
        "sunset_week_summary": week_summary["sunset"],
        "days": days,
    }
