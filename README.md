# First Light

Scores the next seven sunrises so you know which morning is worth the alarm.

## Run it

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
export GEMINI_API_KEY=your-key-here     # free from aistudio.google.com

python cli.py                           # ranked week in the terminal
uvicorn app:app --reload                # then open http://127.0.0.1:8000
```

`python cli.py 51.47 -0.37` scores a different location. Add `--force`
to re-score a location you've already run today.

## How it works

Open-Meteo gives cloud cover split by altitude. That split is the whole
idea: mid and high cloud around 20-70% catches the pink light, while low
cloud above ~40% puts grey murk on the horizon and kills it. A clear sky
scores middling — pleasant, but plain.

Each day gets two scores. `rules_score` is a hand-written baseline that is
saved but never displayed. The model score is what the app shows. Keeping
both means you can look back later and see where they disagreed, and who
was right.

Scoring runs at most once per day per location; results are written to
`scores/YYYY-MM-DD_LAT_LON.json` and the UI only ever reads from there.

## The training set

Every past morning in the UI has two buttons. Tapping one appends a line to
`log.jsonl` pairing your verdict with the raw features and the predicted
score. That file is the point of this project: after a few months it's a
real, personally-labelled dataset for a model that knows your sky.

## Tests

```bash
pytest
```

Covers `build_report`'s scoring/fallback logic and the `/api/forecast` and
`/api/rate` routes, all with fake scorers and a faked `load_or_create` -
no network calls, no API keys, nothing written to disk.
