# Helena Alpha Engine

A fully automated daily market briefing system. Every morning it pulls live market data, 
structures it as a sell-side research briefing, narrates it with AI, and delivers it as 
an audio podcast to my phone via Telegram — no manual input required.

## What it does

- Fetches live market data across equities, macro, and cross-asset via Finnhub
- Structures the briefing across 7 sections modelled on sell-side research format
- Generates narration using Claude Opus (Anthropic)
- Converts to audio using OpenAI TTS
- Generates cover art using DALL-E
- Delivers the final podcast to Telegram automatically every morning
- Runs on a daily GitHub Actions cron job (8 AM UTC)

## Tech stack

- Python / FastAPI
- Anthropic Claude Opus
- OpenAI (TTS + DALL-E)
- Finnhub API
- Telegram Bot API
- GitHub Actions

## Why I built it

I wanted a way to stay on top of markets every day in a format that actually fits into 
a morning routine. Building it also forced me to understand how sell-side research is 
structured — macro context, cross-asset moves, sector rotation, positioning — by having 
to prompt for it systematically.

## Setup

1. Clone the repo
2. Add your API keys to a `.env` file:
```
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
FINNHUB_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```
3. Install dependencies: `pip install -r requirements.txt`
4. Run locally: `uvicorn app:app --reload`
