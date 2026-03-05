from datetime import date
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse
import os
import time
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

        items.append(
            {
                "symbol": symbol,
                "last": last,
                "prev_close": prev,
                "pct_change": pct_change,
            }
        )

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
