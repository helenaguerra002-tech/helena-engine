from fastapi import FastAPI
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
"AZN"
],

"emerging": [
"NU",
"VALE",
"PETR4.SA",
"SBSP3.SA",
"MELI",
"WEGE3.SA"
],

"macro": [
"SPX",
"NDX",
"USO",
"US10Y"
]
LIST_ALIASES = {
    "brazil_em": "emerging",
    "brazil": "emerging",
    "em": "emerging",
    "br": "emerging",
}
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
            <li><code>GET /</code> — this page</li>
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
from datetime import date
from fastapi import Query

@app.get("/watchlist/brief")
def watchlist_brief(list: str = Query("global")):

key = LIST_ALIASES.get(list, list)
wl = WATCHLISTS.get(key)

    if wl is None:
        return {
            "error": "invalid list",
            "available_lists": list(WATCHLISTS.keys())
        }

    brief_lines = []
    brief_lines.append(f"Helena Alpha Engine — {list.upper()} Brief ({date.today().isoformat()})")
    brief_lines.append("")

    for t in wl:
        brief_lines.append(f"{t}")

    return {
        "date": date.today().isoformat(),
        "watchlist": wl,
        "brief": "\n".join(brief_lines)
    }
