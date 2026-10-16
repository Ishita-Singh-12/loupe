# Open Deep Research launcher (loupe Phase 3)

Runs [LangChain's Open Deep Research](https://github.com/langchain-ai/open_deep_research)
on your machine with the Gemini API and Tavily search, and records every run as a
trace on the hosted loupe dashboard. Type a question, hit Send, then open the
dashboard to inspect every model call, search and compression step as spans.

## Quickstart (Windows)

```bat
git clone https://github.com/Ishita-Singh-12/loupe.git
cd loupe
odr\run.bat
```

First run creates a virtual environment, installs dependencies and asks for three
keys (stored locally in `odr/.env`, git-ignored, never printed):

- `GOOGLE_API_KEY` - Gemini key from [Google AI Studio](https://aistudio.google.com/apikey)
- `TAVILY_API_KEY` - from [app.tavily.com](https://app.tavily.com)
- `LOUPE_INGEST_KEY` - the loupe backend write key

Then open http://127.0.0.1:8788, type a question, hit Send. A run takes a few
minutes; the finished report appears on the page and in `odr/reports/`, and the
trace appears on the dashboard at https://loupe-y3em.onrender.com.

macOS/Linux: `sh odr/run.sh`. Terminal mode instead of the web UI: `--cli`.
Preflight spec check only: `--check`.

## How it works

- The launcher passes `LoupeCallbackHandler` into each agent run
  (`config={"callbacks": [...]}`), so every chain, model call and tool call in
  the LangGraph run becomes a parent-linked span under one trace.
- The loupe SDK batches spans to `POST /api/spans` on the hosted backend with
  the ingest key; export failures never crash the run.
- The model runs on the Gemini API (default `google_genai:gemini-3.8-flash` with an automatic one-time retry on `gemini-3.1-flash-lite` if the daily free quota is hit,
  override with `LOUPE_ODR_MODEL`); the laptop only runs the agent code, so CPU/RAM
  needs are modest (Python 3.10+, ~4 GB RAM, ~2 GB disk for the venv).
- Free-tier friendly defaults: `LOUPE_ODR_MAX_CONCURRENCY=2`,
  `LOUPE_ODR_MAX_ITERATIONS=6`. Clarifying questions are disabled so each Send is
  one complete run.
