from datetime import date, timedelta
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse
import anthropic
import os
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

anthropic_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

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
                }
            )
            continue

        last = quote.get("c")
        prev = quote.get("pc")

        pct_change = None
        if isinstance(last, (int, float)) and isinstance(prev, (int, float)) and prev != 0:
            pct_change = (last - prev) / prev * 100

        items.append({"symbol": symbol, "last": last, "prev_close": prev, "pct_change": pct_change})
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
            snapshot_lines.append(f"  {item['symbol']}: ${item['last']:.2f} ({item['pct_change']:+.2f}%)")
        else:
            snapshot_lines.append(f"  {item['symbol']}: data unavailable")

    news_sections = []
    for symbol, articles in news_by_symbol.items():
        if articles:
            headlines = "\n".join(f"    • {a['headline']}" for a in articles)
            news_sections.append(f"  {symbol}:\n{headlines}")
        else:
            news_sections.append(f"  {symbol}: no recent news")

    prompt = f"""You are Helena's AI market analyst. Today is {date.today().isoformat()}.

{key.upper()} WATCHLIST SNAPSHOT:
{chr(10).join(snapshot_lines)}

RECENT NEWS FOR TOP MOVERS:
{chr(10).join(news_sections) if news_sections else "No recent news available."}

Write a concise 2-3 paragraph investment intelligence briefing. Cover:
1. What is moving and why, based on the news context
2. What macro or sector signal this suggests
3. One specific hypothesis worth investigating further

Be direct and sharp. Write like an analyst at a top hedge fund, not a financial advisor. No disclaimers."""

    async with anthropic_client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=1024,
        thinking={"type": "adaptive"},
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
