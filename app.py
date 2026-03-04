from fastapi import FastAPI
from fastapi.responses import HTMLResponse

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
