from datetime import date, timedelta
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB, ID3NoHeaderError
import anthropic
import openai
import base64
import os
import tempfile
import httpx

WATCHLISTS = {
    "global": [
        "NVDA",
        "ASML",
        "MSFT",
        "LLY",
        "COST",
        "RYAAY",
        "TDG",
        "AZN",
    ],
    "emerging": [
        "NU",
        "VALE",
        "PETR4.SA",
        "SBSP3.SA",
        "MELI",
        "WEGE3.SA",
    ],
    # NOTE: Finnhub is much more reliable with tradeable tickers (ETFs) than index codes.
    # If your macro list is currently SPX/NDX/US10Y, switch to these for reliability:
    "macro": [
        "SPY",  # S&P 500 ETF
        "QQQ",  # Nasdaq 100 ETF
        "USO",  # Oil ETF
        "IEF",  # 7-10Y US Treasury ETF (proxy for 10Y)
    ],
}

# Optional: allows you to request brazil_em but map it to emerging
LIST_ALIASES = {
    "brazil_em": "emerging",
}

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

anthropic_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None
openai_client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

app = FastAPI(title="Helena Alpha Engine v1")


@app.get("/", response_class=HTMLResponse)
def root():
    return """
    <html>
      <head>
        <title>Helena Alpha Engine v1</title>
        <style>
          body { font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
                 margin: 40px; max-width: 860px; line-height: 1.5; }
          code { background: #f4f4f4; padding: 2px 6px; border-radius: 6px; }
          .card { border: 1px solid #e6e6e6; border-radius: 12px; padding: 18px 20px; margin-top: 18px; }
          a { text-decoration: none; }
          li { margin: 6px 0; }
          .muted { color: #666; font-size: 14px; }
        </style>
      </head>
      <body>
        <h1>Helena Alpha Engine v1</h1>
        <p>A lightweight investment intelligence backend (signals → hypotheses → predictions → evaluation).</p>

        <div class="card">
          <h3>Endpoints</h3>
          <ul>
            <li><a href="/docs">/docs</a> — interactive API documentation</li>
            <li><a href="/health">/health</a> — health check</li>
            <li><a href="/watchlist/brief?watchlist=global">/watchlist/brief?watchlist=global</a> — text brief</li>
            <li><a href="/watchlist/snapshot?watchlist=global">/watchlist/snapshot?watchlist=global</a> — live snapshot (needs FINNHUB_API_KEY)</li>
            <li><a href="/watchlist/digest?watchlist=global">/watchlist/digest?watchlist=global</a> — snapshot + news + AI narrative (needs both API keys)</li>
            <li><a href="/watchlist/podcast?watchlist=global">/watchlist/podcast?watchlist=global</a> — single watchlist as MP3 audio (needs all 3 API keys)</li>
            <li><a href="/watchlist/podcast/daily">/watchlist/podcast/daily</a> — all 3 watchlists combined, with DALL-E cover art (needs all 3 API keys)</li>
            <li><code>GET /</code> — this page</li>
          </ul>
          <p class="muted">Tip: try <code>watchlist=macro</code> or <code>watchlist=brazil_em</code>.</p>
        </div>

        <div class="card">
          <h3>Watchlists</h3>
          <ul>
            <li><code>global</code></li>
            <li><code>emerging</code> (alias: <code>brazil_em</code>)</li>
            <li><code>macro</code></li>
          </ul>
        </div>

        <div class="card">
          <h3>Status</h3>
          <p>If you can see this page, the service is running.</p>
        </div>
      </body>
    </html>
    """


@app.get("/health")
def health():
    return {"status": "ok", "service": "helena-alpha-engine"}


@app.get("/watchlist/brief")
def watchlist_brief(watchlist: str = Query("global")):
    key = LIST_ALIASES.get(watchlist, watchlist)
    wl = WATCHLISTS.get(key)

    if wl is None:
        return {
            "error": "invalid watchlist",
            "available_watchlists": list(WATCHLISTS.keys()),
            "aliases": LIST_ALIASES,
        }

    brief_lines = [
        f"Helena Alpha Engine — {key.upper()} Brief ({date.today().isoformat()})",
        "",
        *wl,
    ]

    return {
        "date": date.today().isoformat(),
        "watchlist_name": key,
        "watchlist": wl,
        "brief": "\n".join(brief_lines),
    }


async def get_quote(symbol: str) -> dict:
    url = "https://finnhub.io/api/v1/quote"
    params = {"symbol": symbol, "token": FINNHUB_API_KEY}

    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        return r.json()


async def get_metric(symbol: str) -> dict:
    url = "https://finnhub.io/api/v1/stock/metric"
    params = {"symbol": symbol, "metric": "all", "token": FINNHUB_API_KEY}

    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        return r.json()


async def get_news(symbol: str, days: int = 3) -> list[dict]:
    today = date.today()
    from_date = (today - timedelta(days=days)).isoformat()
    url = "https://finnhub.io/api/v1/company-news"
    params = {"symbol": symbol, "from": from_date, "to": today.isoformat(), "token": FINNHUB_API_KEY}

    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        return r.json()


async def _fetch_snapshot_items(wl: list[str]) -> list[dict]:
    items = []
    for symbol in wl:
        try:
            quote = await get_quote(symbol)
        except Exception as e:
            items.append(
                {
                    "symbol": symbol,
                    "error": f"quote_fetch_failed: {str(e)}",
                    "last": None,
                    "prev_close": None,
                    "pct_change": None,
                    "volume": None,
                    "avg_volume_10d": None,
                    "volume_spike": False,
                }
            )
            continue

        last = quote.get("c")
        prev = quote.get("pc")
        volume = quote.get("v")  # current day volume in shares

        pct_change = None
        if isinstance(last, (int, float)) and isinstance(prev, (int, float)) and prev != 0:
            pct_change = (last - prev) / prev * 100

        # Fetch average volume; Finnhub returns 10DayAverageTradingVolume in millions
        avg_volume_10d = None
        volume_spike = False
        try:
            metric = await get_metric(symbol)
            avg_raw = metric.get("metric", {}).get("10DayAverageTradingVolume")
            if isinstance(avg_raw, (int, float)) and avg_raw > 0:
                avg_volume_10d = avg_raw * 1_000_000  # convert to shares
                if isinstance(volume, (int, float)) and volume > 2 * avg_volume_10d:
                    volume_spike = True
        except Exception:
            pass  # avg vol is best-effort; don't fail the whole snapshot

        items.append({
            "symbol": symbol,
            "last": last,
            "prev_close": prev,
            "pct_change": pct_change,
            "volume": volume,
            "avg_volume_10d": avg_volume_10d,
            "volume_spike": volume_spike,
        })
    return items


@app.get("/watchlist/snapshot")
async def watchlist_snapshot(watchlist: str = Query("global")):
    """
    Returns a live snapshot (last price + % change) for a watchlist using Finnhub.
    """

    if not FINNHUB_API_KEY:
        raise HTTPException(status_code=500, detail="FINNHUB_API_KEY not configured")

    key = LIST_ALIASES.get(watchlist, watchlist)
    wl = WATCHLISTS.get(key)

    if wl is None:
        return {
            "error": "invalid watchlist",
            "available_watchlists": list(WATCHLISTS.keys()),
            "aliases": LIST_ALIASES,
        }

    items = await _fetch_snapshot_items(wl)

    movers = sorted(
        [x for x in items if isinstance(x.get("pct_change"), (int, float))],
        key=lambda x: abs(x["pct_change"]),
        reverse=True,
    )[:5]

    return {
        "date": date.today().isoformat(),
        "watchlist": key,
        "top_movers": movers,
        "items": items,
    }


@app.get("/watchlist/digest")
async def watchlist_digest(watchlist: str = Query("global")):
    """
    Live snapshot + recent news for top movers + Claude-generated investment narrative.
    """
    if not FINNHUB_API_KEY:
        raise HTTPException(status_code=500, detail="FINNHUB_API_KEY not configured")
    if not anthropic_client:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")

    key = LIST_ALIASES.get(watchlist, watchlist)
    wl = WATCHLISTS.get(key)

    if wl is None:
        return {
            "error": "invalid watchlist",
            "available_watchlists": list(WATCHLISTS.keys()),
            "aliases": LIST_ALIASES,
        }

    # Fetch prices
    items = await _fetch_snapshot_items(wl)

    top_movers = sorted(
        [x for x in items if isinstance(x.get("pct_change"), (int, float))],
        key=lambda x: abs(x["pct_change"]),
        reverse=True,
    )[:3]

    # Fetch recent news for each top mover
    news_by_symbol: dict[str, list[dict]] = {}
    for mover in top_movers:
        symbol = mover["symbol"]
        try:
            articles = await get_news(symbol)
            news_by_symbol[symbol] = [
                {"headline": a.get("headline", ""), "source": a.get("source", "")}
                for a in articles[:4]
                if a.get("headline")
            ]
        except Exception:
            news_by_symbol[symbol] = []

    # Build prompt
    snapshot_lines = []
    for item in items:
        if item.get("pct_change") is not None:
            spike_flag = " [VOLUME SPIKE]" if item.get("volume_spike") else ""
            snapshot_lines.append(f"  {item['symbol']}: ${item['last']:.2f} ({item['pct_change']:+.2f}%){spike_flag}")
        else:
            snapshot_lines.append(f"  {item['symbol']}: data unavailable")

    news_sections = []
    for symbol, articles in news_by_symbol.items():
        if articles:
            headlines = "\n".join(f"    • {a['headline']}" for a in articles)
            news_sections.append(f"  {symbol}:\n{headlines}")
        else:
            news_sections.append(f"  {symbol}: no recent news")

    prompt = f"""You are Helena's personal market teacher. Today is {date.today().isoformat()}.

{key.upper()} WATCHLIST SNAPSHOT:
{chr(10).join(snapshot_lines)}

RECENT NEWS FOR TOP MOVERS:
{chr(10).join(news_sections) if news_sections else "No recent news available."}

Write a 3-paragraph briefing that genuinely teaches Helena what happened today. She is smart but has no finance background yet.

Paragraph 1: What moved and why — for each big mover, say in one sentence what the company actually does, then explain the price move using a real-world analogy (e.g. "think of it like a coffee shop that just announced their beans got cheaper — customers expect lower prices and more profit, so more people want to own a piece of the business"). Explain the cause clearly.

Paragraph 2: What this could mean going forward — what are the possible consequences? Who else gets affected? Use concrete examples a non-expert would relate to (jobs, prices at the store, other companies in the same industry).

Paragraph 3: One genuinely interesting question worth researching, explained in a way that makes her curious to learn more.

Rules: Write in flowing paragraphs. Never use a finance term without immediately explaining it in plain words. Use analogies from everyday life — food, school, weather, sports, anything relatable. Be warm and enthusiastic, like a professor who loves this topic."""

    async with anthropic_client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        final = await stream.get_final_message()

    narrative = next((b.text for b in final.content if b.type == "text"), "")

    return {
        "date": date.today().isoformat(),
        "watchlist": key,
        "top_movers": top_movers,
        "news": news_by_symbol,
        "narrative": narrative,
        "items": items,
    }


@app.get("/watchlist/podcast")
async def watchlist_podcast(watchlist: str = Query("global")):
    """
    Full digest pipeline → OpenAI TTS → returns MP3 audio of the investment briefing.
    Requires FINNHUB_API_KEY, ANTHROPIC_API_KEY, and OPENAI_API_KEY.
    """
    if not FINNHUB_API_KEY:
        raise HTTPException(status_code=500, detail="FINNHUB_API_KEY not configured")
    if not anthropic_client:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")
    if not openai_client:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not configured — set it in Render environment variables")

    key = LIST_ALIASES.get(watchlist, watchlist)
    wl = WATCHLISTS.get(key)

    if wl is None:
        raise HTTPException(status_code=400, detail=f"Unknown watchlist '{watchlist}'. Available: {list(WATCHLISTS.keys())}")

    # Reuse digest logic to get narrative
    items = await _fetch_snapshot_items(wl)

    top_movers = sorted(
        [x for x in items if isinstance(x.get("pct_change"), (int, float))],
        key=lambda x: abs(x["pct_change"]),
        reverse=True,
    )[:3]

    news_by_symbol: dict[str, list[dict]] = {}
    for mover in top_movers:
        symbol = mover["symbol"]
        try:
            articles = await get_news(symbol)
            news_by_symbol[symbol] = [
                {"headline": a.get("headline", ""), "source": a.get("source", "")}
                for a in articles[:4]
                if a.get("headline")
            ]
        except Exception:
            news_by_symbol[symbol] = []

    snapshot_lines = []
    for item in items:
        if item.get("pct_change") is not None:
            spike_flag = " — volume spike detected" if item.get("volume_spike") else ""
            snapshot_lines.append(f"  {item['symbol']}: ${item['last']:.2f} ({item['pct_change']:+.2f}%){spike_flag}")
        else:
            snapshot_lines.append(f"  {item['symbol']}: data unavailable")

    news_sections = []
    for symbol, articles in news_by_symbol.items():
        if articles:
            headlines = "\n".join(f"    • {a['headline']}" for a in articles)
            news_sections.append(f"  {symbol}:\n{headlines}")
        else:
            news_sections.append(f"  {symbol}: no recent news")

    prompt = f"""You are Helena's personal market teacher. Today is {date.today().isoformat()}.

{key.upper()} WATCHLIST SNAPSHOT:
{chr(10).join(snapshot_lines)}

RECENT NEWS FOR TOP MOVERS:
{chr(10).join(news_sections) if news_sections else "No recent news available."}

Write a 3-paragraph briefing that genuinely teaches Helena what happened today. She is smart but has no finance background yet.

Paragraph 1: What moved and why — for each big mover, say in one sentence what the company actually does, then explain the price move using a real-world analogy (e.g. "think of it like a coffee shop that just announced their beans got cheaper — customers expect lower prices and more profit, so more people want to own a piece of the business"). Explain the cause clearly.

Paragraph 2: What this could mean going forward — what are the possible consequences? Who else gets affected? Use concrete examples a non-expert would relate to (jobs, prices at the store, other companies in the same industry).

Paragraph 3: One genuinely interesting question worth researching, explained in a way that makes her curious to learn more.

Rules: Write in flowing paragraphs. Never use a finance term without immediately explaining it in plain words. Use analogies from everyday life — food, school, weather, sports, anything relatable. Be warm and enthusiastic, like a professor who loves this topic."""

    async with anthropic_client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        final = await stream.get_final_message()

    narrative = next((b.text for b in final.content if b.type == "text"), "")

    # Convert narrative to speech
    tts_input = f"Helena Alpha Engine — {key.upper()} briefing for {date.today().strftime('%B %d, %Y')}.\n\n{narrative}"

    response = await openai_client.audio.speech.create(
        model="tts-1",
        voice="alloy",
        input=tts_input,
    )

    audio_bytes = response.content

    return StreamingResponse(
        iter([audio_bytes]),
        media_type="audio/mpeg",
        headers={"Content-Disposition": f'attachment; filename="helena-{key}-{date.today().isoformat()}.mp3"'},
    )


def _embed_mp3_metadata(audio_bytes: bytes, title: str, image_bytes: bytes | None) -> bytes:
    """Write ID3 tags (title, artist, album, cover art) into an MP3 and return the result."""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name
    try:
        try:
            tags = ID3(tmp_path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.add(TIT2(encoding=3, text=title))
        tags.add(TPE1(encoding=3, text="Helena Alpha Engine"))
        tags.add(TALB(encoding=3, text="Helena Daily Briefing"))
        if image_bytes:
            tags.add(APIC(encoding=3, mime="image/png", type=3, desc="Cover", data=image_bytes))
        tags.save(tmp_path)
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        os.unlink(tmp_path)


def _chunk_text_for_tts(text: str, max_chars: int = 4000) -> list[str]:
    """Split text into chunks at sentence boundaries, each under max_chars."""
    chunks = []
    remaining = text.strip()
    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break
        truncated = remaining[:max_chars]
        last_end = max(truncated.rfind('.'), truncated.rfind('!'), truncated.rfind('?'))
        if last_end > 0:
            chunks.append(remaining[:last_end + 1].strip())
            remaining = remaining[last_end + 1:].strip()
        else:
            chunks.append(truncated)
            remaining = remaining[max_chars:].strip()
    return [c for c in chunks if c]


async def _run_daily_podcast() -> tuple[bytes, str]:
    """
    Core generation logic: fetch data → Claude narrative → DALL-E art → MP3 bytes.
    Returns (audio_bytes, episode_title).
    Raises HTTPException if required API keys are missing.
    """
    if not FINNHUB_API_KEY:
        raise HTTPException(status_code=500, detail="FINNHUB_API_KEY not configured")
    if not anthropic_client:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")
    if not openai_client:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not configured — set it in Render environment variables")

    # Fetch all 3 watchlists
    all_items: dict[str, list[dict]] = {}
    all_top_movers: dict[str, list[dict]] = {}
    for wl_name, tickers in WATCHLISTS.items():
        items = await _fetch_snapshot_items(tickers)
        all_items[wl_name] = items
        all_top_movers[wl_name] = sorted(
            [x for x in items if isinstance(x.get("pct_change"), (int, float))],
            key=lambda x: abs(x["pct_change"]),
            reverse=True,
        )[:2]

    # Fetch news for top movers across all watchlists
    news_by_symbol: dict[str, list[dict]] = {}
    for movers in all_top_movers.values():
        for mover in movers:
            symbol = mover["symbol"]
            try:
                articles = await get_news(symbol)
                news_by_symbol[symbol] = [
                    {"headline": a.get("headline", ""), "source": a.get("source", "")}
                    for a in articles[:3]
                    if a.get("headline")
                ]
            except Exception:
                news_by_symbol[symbol] = []

    # Build data sections for the prompt
    wl_labels = {
        "global": "GLOBAL STOCKS (major US and international companies)",
        "emerging": "EMERGING MARKETS (companies from fast-growing economies, especially Brazil)",
        "macro": "MACRO INDICATORS (funds that track the broader market — think of these as the economy's vital signs)",
    }

    data_sections = []
    for wl_name, items in all_items.items():
        snapshot_lines = []
        for item in items:
            if item.get("pct_change") is not None:
                spike = " — unusually high trading activity today" if item.get("volume_spike") else ""
                snapshot_lines.append(f"  {item['symbol']}: ${item['last']:.2f} ({item['pct_change']:+.2f}%){spike}")
            else:
                snapshot_lines.append(f"  {item['symbol']}: data unavailable")

        news_lines = []
        for mover in all_top_movers[wl_name]:
            symbol = mover["symbol"]
            articles = news_by_symbol.get(symbol, [])
            if articles:
                headlines = "\n".join(f"    • {a['headline']}" for a in articles)
                news_lines.append(f"  {symbol} news:\n{headlines}")
            else:
                news_lines.append(f"  {symbol}: no recent news")

        data_sections.append(
            f"{wl_labels[wl_name]}:\n"
            + "\n".join(snapshot_lines)
            + "\n\nTop movers news:\n"
            + "\n".join(news_lines)
        )

    prompt = f"""You are the voice of Helena Alpha Engine, a daily market intelligence briefing. Today is {date.today().isoformat()}.

Here is today's market data:

---
{(chr(10) + "---" + chr(10)).join(data_sections)}
---

Write a spoken daily briefing of approximately 1,800–2,000 words, structured as seven sections. This briefing is for Helena, a finance student preparing for a career at a top-tier hedge fund. She is analytically sharp and motivated. The briefing should be sophisticated enough that a professional could overhear it and find it credible, while still explaining reasoning clearly enough that Helena can follow and learn.

Do NOT open with "Helena", "Daily Market Briefing", or today's date. Begin the first sentence of Section 1 directly.

---

SECTION 1 — MACRO REGIME (~200 words)
Open every briefing here. State the current macro regime clearly: where the 10-year Treasury yield is trading (use IEF price movement as your proxy — if IEF is up, yields are falling, and vice versa), whether volatility is elevated or subdued based on equity behaviour, and whether the dollar appears to be strengthening or weakening based on macro context and the moves you see in the data. State whether the overall tone is risk-on or risk-off and give one sentence explaining why. This is the weather report — set the context before anything else.
Example register: "The 10-year is holding near [X]%, vol is subdued with the S&P grinding higher, and the dollar is softening slightly — that combination historically favours risk assets, particularly growth and tech."

SECTION 2 — LEAD STORY (~250 words)
Identify the single most market-moving development in today's data. Lead with why it matters to positioning, not just what happened. Use one analogy here if helpful, but draw it from financial history, policy precedent, or industry dynamics — not consumer product comparisons. The analogy should hold up if a professional overheard it. Example register: comparing a central bank's action to the Fed's 2011 currency swap lines, or framing a sector move as similar to the 2013 taper tantrum rotation.

SECTION 3 — GLOBAL EQUITIES ROUNDUP (~350 words)
Cover 4–6 names across the global and emerging market watchlists. For each name: what it did, why, and what it signals about the broader sector or macro theme. Connect the move beyond the individual stock. If ASML is up, connect it to EUV demand, AI capex cycles, and what it implies for the semis complex. If a Brazilian name is moving, connect it to China's growth pipeline or commodity demand. Include percentage moves and price levels. Every move should teach something about how markets work.

SECTION 4 — MACRO DEEP DIVE (~350 words)
Pick the single most important macro story visible in today's data — a commodity move, a rate signal from IEF or USO, a divergence between geographies — and go deeper than the headline. Cover: what the move implies about what's currently priced in, what data or event would change the picture, and how this interacts with equities or credit. Where relevant, mention positioning dynamics, term structure, or cross-asset correlations. This section earns credibility. Be specific and quantitative.

SECTION 5 — CONNECTING THE DOTS (~250 words)
Synthesise the session into 2–3 cross-asset themes. This section should feel like a coherent thesis, not a tour of markets. Use connective language: "the common thread here is...", "what the market is pricing is...", "the notable divergence is...". Identify any unusual relationships in today's data — oil falling while tech rises, bonds selling off while equities hold — and explain what they historically signal.

SECTION 6 — THE ANALYST'S TAKE (~250 words)
This is the most important section. Structure it as four parts:
(a) Your directional thesis for the next session or week, stated plainly.
(b) One supporting data point or historical parallel — include numbers.
(c) What would specifically invalidate this view — be concrete, not vague.
(d) One actionable implication: what a manager might do with this information.
Tone: a senior analyst briefing a motivated junior colleague. Confident, specific, and honest about uncertainty. No vague hedges. No "it could go either way." State a view and defend it.

SECTION 7 — WHAT TO WATCH (~150 words)
Close with 2–3 specific catalysts in the next 24–48 hours that could confirm or break today's thesis. Name actual events: earnings releases, Fed speakers, scheduled data (CPI, PMI, jobs), geopolitical flashpoints. Tell Helena what to look for — not just that an event is happening, but what outcome would be bullish or bearish and why.

---

STYLE RULES:
- Write in flowing spoken paragraphs. No bullet points, no headers in the output — this is read aloud.
- Every finance term must be explained in plain language in the same sentence the first time it appears.
- Analogies should come from financial history, policy precedent, or macro dynamics — not everyday consumer products.
- Include real numbers throughout: index levels, percentage moves, yield levels, price changes.
- Tone is confident, collegial, and direct — like a smart analyst talking to a motivated intern. Not a presenter performing for a general audience.
- No filler phrases: no "let's unpack", "here's the surprising thing", "great question", or "fascinating". Get to the point in every section.
- Do not use bullet points or numbered lists anywhere in the output."""

    async with anthropic_client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        final = await stream.get_final_message()

    narrative = next((b.text for b in final.content if b.type == "text"), "")

    # Generate DALL-E cover art based on today's top movers
    mover_themes = []
    for wl_name, movers in all_top_movers.items():
        for m in movers[:1]:
            if isinstance(m.get("pct_change"), (int, float)):
                direction = "climbing" if m["pct_change"] > 0 else "falling"
                mover_themes.append(f"{m['symbol']} {direction}")

    theme_desc = ", ".join(mover_themes) if mover_themes else "mixed market movements"
    art_prompt = (
        f"Abstract minimalist artwork for a daily financial podcast episode cover. "
        f"Today's market theme: {theme_desc}. "
        "Warm golden yellow and amber color palette, clean geometric shapes suggesting movement and flow, "
        "no text, no numbers, no letters, no words, modern and elegant, square format."
    )

    image_bytes = None
    try:
        img_response = await openai_client.images.generate(
            model="dall-e-3",
            prompt=art_prompt,
            size="1024x1024",
            response_format="b64_json",
            n=1,
        )
        image_bytes = base64.b64decode(img_response.data[0].b64_json)
    except Exception:
        pass  # cover art is best-effort — audio still returns if DALL-E fails

    # Convert narrative to speech — chunked to handle OpenAI's 4096-char-per-request limit
    intro = f"Helena's Daily Market Briefing — {date.today().strftime('%B %d, %Y')}.\n\n"
    full_text = intro + narrative
    tts_chunks = _chunk_text_for_tts(full_text)
    audio_parts = []
    for chunk in tts_chunks:
        chunk_response = await openai_client.audio.speech.create(
            model="tts-1",
            voice="alloy",
            input=chunk,
        )
        audio_parts.append(chunk_response.content)
    audio_bytes = b"".join(audio_parts)

    # Embed cover art and metadata into the MP3
    episode_title = f"Helena Daily Briefing — {date.today().isoformat()}"
    audio_bytes = _embed_mp3_metadata(audio_bytes, title=episode_title, image_bytes=image_bytes)

    return audio_bytes, episode_title


async def _send_to_telegram(audio_bytes: bytes, title: str) -> bool:
    """Send the daily podcast MP3 to Telegram. Returns True on success."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendAudio"
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "title": title, "performer": "Helena Alpha Engine"},
            files={"audio": ("briefing.mp3", audio_bytes, "audio/mpeg")},
        )
    return r.status_code == 200


@app.get("/watchlist/podcast/daily")
async def watchlist_podcast_daily():
    """
    Combined briefing across ALL 3 watchlists → one Claude narrative → DALL-E cover art → MP3.
    Requires FINNHUB_API_KEY, ANTHROPIC_API_KEY, and OPENAI_API_KEY.
    """
    audio_bytes, episode_title = await _run_daily_podcast()
    return StreamingResponse(
        iter([audio_bytes]),
        media_type="audio/mpeg",
        headers={"Content-Disposition": f'attachment; filename="helena-daily-{date.today().isoformat()}.mp3"'},
    )


@app.post("/podcast/trigger-daily")
async def podcast_trigger_daily():
    """
    Called by the daily scheduler — generates the podcast and pushes it to Telegram.
    Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars on Render.
    """
    audio_bytes, episode_title = await _run_daily_podcast()
    telegram_ok = await _send_to_telegram(audio_bytes, episode_title)
    return {"status": "ok", "title": episode_title, "telegram_sent": telegram_ok}
