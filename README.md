# Golden Hour

Scores the next seven sunrises/sunsets so you now which one is most worth seeing.

## Run it

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
export GEMINI_API_KEY=your-key-here     # free from aistudio.google.com

uvicorn app:app --reload                # then open http://127.0.0.1:8000
```

## How it works

Gets weather data with a weather API which is then fed into an LLM as a prompt, outputting
a score and message about each day. A automatic self-improvement loop with the user feedback 
means that it continues to learn without human intervention

## Tests

```bash
pytest
```