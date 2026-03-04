from datetime import date
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse

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
    "macro": [
        "SPX",
        "NDX",
        "USO",
        "US10Y",
    ],
}

# Optional: allows you to request brazil_em but map it to emerging
LIST_ALIASES = {
    "brazil_em": "emerging",
}

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
            <li><a href="/watchlist/brief?watchlist=global">/watchlist/brief?watchlist=global</a> — sample brief</li>
            <li><code>GET /</code> — this page</li>
          </ul>
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

