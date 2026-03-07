# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

Helena Alpha Engine is an AI-powered investment intelligence backend for Helena (a student preparing for Point72 internships). It fetches live market data, detects signals, and will eventually generate daily briefings/podcasts. The pipeline is: market data → signal detection → investment hypotheses → macro narrative → daily briefing.

## Tech Stack

- **FastAPI** + **uvicorn** — single-file API (`app.py`)
- **httpx** — async HTTP client for Finnhub API calls
- **Python 3.14**, venv at `.venv/`
- **Deployed on Render** — auto-deploys from `main` branch on GitHub
- **Market data**: Finnhub REST API, key via `FINNHUB_API_KEY` env var
- **AI narrative**: Anthropic SDK (`anthropic` package), `AsyncAnthropic` client, key via `ANTHROPIC_API_KEY` env var

## Development Commands

```bash
# Activate venv
source .venv/bin/activate

# Run locally
uvicorn app:app --reload

# Install dependencies
pip install -r requirements.txt
```

No test suite exists yet. Manual testing is done via the `/docs` interactive UI or direct curl calls.

## Architecture

The entire backend lives in `app.py` (single-file). Key globals:

- `WATCHLISTS` dict — maps name → list of ticker symbols
- `LIST_ALIASES` dict — maps alias → canonical watchlist name (e.g. `brazil_em` → `emerging`)
- `FINNHUB_API_KEY` / `ANTHROPIC_API_KEY` — read from env at startup; `anthropic_client` is `None` if key is missing (endpoints return HTTP 500)

Shared helper `_fetch_snapshot_items(wl)` fetches quotes sequentially (not `asyncio.gather`) to respect Finnhub rate limits. Finnhub quote fields: `c` (last price), `pc` (prev close). News via `/company-news` endpoint, last 3 days.

The `/watchlist/digest` endpoint uses `claude-opus-4-6` with `thinking: {type: "adaptive"}` and streaming (`stream.get_final_message()`) to generate a 2-3 paragraph analyst narrative from prices + top-mover headlines.

## Watchlists

| Name | Tickers |
|------|---------|
| `global` | NVDA, ASML, MSFT, LLY, COST, RYAAY, TDG, AZN |
| `emerging` (alias: `brazil_em`) | NU, VALE, PETR4.SA, SBSP3.SA, MELI, WEGE3.SA |
| `macro` | SPY, QQQ, USO, IEF |

Brazilian tickers use `.SA` suffix for Finnhub. Prefer ETF proxies over index codes for Finnhub reliability (e.g. SPY over SPX).

## Planned Features (Priority Order)

1. **Volume spike detection** — need to add `v` (volume) from Finnhub quote + avg vol from `/stock/metric`, flag vol > 2x avg
2. **Podcast/audio generation** — OpenAI TTS or ElevenLabs, reading out the `/watchlist/digest` narrative

## Deployment Notes

- `FINNHUB_API_KEY` and `ANTHROPIC_API_KEY` must both be set in Render's environment variables
- `/watchlist/snapshot` requires `FINNHUB_API_KEY`; `/watchlist/digest` requires both
- Render auto-deploys on push to `main`
