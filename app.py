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

TICKER_NAMES = {
    "SPY": "S&P 500", "QQQ": "Nasdaq 100", "USO": "petróleo (USO)",
    "IEF": "Títulos do Tesouro americano de 10 anos",
    "NVDA": "Nvidia", "ASML": "ASML", "MSFT": "Microsoft",
    "LLY": "Eli Lilly", "COST": "Costco", "RYAAY": "Ryanair",
    "TDG": "TransDigm", "AZN": "AstraZeneca", "NU": "Nubank",
    "VALE": "Vale", "PETR4.SA": "Petrobras", "SBSP3.SA": "Sabesp",
    "MELI": "MercadoLibre", "WEGE3.SA": "WEG",
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
        model="tts-1-hd",
        voice="nova",
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
                spike = " — atividade de negociação incomum hoje" if item.get("volume_spike") else ""
                name = TICKER_NAMES.get(item['symbol'], item['symbol'])
                snapshot_lines.append(f"  {name} ({item['symbol']}): ${item['last']:.2f} ({item['pct_change']:+.2f}%){spike}")
            else:
                name = TICKER_NAMES.get(item['symbol'], item['symbol'])
                snapshot_lines.append(f"  {name} ({item['symbol']}): dados indisponíveis")

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

    prompt = f"""Você é a voz do Helena Alpha Engine, um boletim diário de inteligência de mercado. Hoje é {date.today().strftime('%d de %B de %Y')}.

Escreva o boletim inteiramente em português do Brasil (pt-BR), no estilo de um apresentador de radiojornalismo financeiro — claro, direto e profissional, como a CBN ou a Rádio Jovem Pan News.

Aqui estão os dados de mercado de hoje:

---
{(chr(10) + "---" + chr(10)).join(data_sections)}
---

Escreva um boletim falado de aproximadamente 1.200 palavras — deve durar exatamente 10 minutos quando lido em voz alta em ritmo natural. Estruture em sete seções. O boletim é para Helena, uma estudante de finanças se preparando para uma carreira em um fundo de hedge de primeira linha. Ela é analítica e motivada. O boletim deve ser sofisticado o suficiente para que um profissional possa ouvi-lo e considerá-lo crível, mas explicado de forma clara o suficiente para que Helena consiga acompanhar e aprender.

Não comece com "Helena", "Boletim Diário" ou a data de hoje. Comece a primeira frase da Seção 1 diretamente.

---

SEÇÃO 1 — CENÁRIO MACRO (~120 palavras)
Comece sempre aqui. Descreva o cenário macro atual: onde o juro do Tesouro americano de 10 anos está operando (use o movimento do preço do IEF como proxy — se o IEF subiu, os juros caíram, e vice-versa), se a volatilidade está elevada ou contida com base no comportamento das ações, e se o tom geral é de apetite ao risco (investidores dispostos a comprar ações e ativos mais arriscados) ou aversão ao risco (investidores buscando segurança em títulos e dólar). Dê uma frase explicando o porquê. Termine com uma frase de transição resumindo o contexto macro antes de avançar.

SEÇÃO 2 — DESTAQUE DO DIA (~200 palavras)
Identifique o único desenvolvimento mais relevante para o mercado nos dados de hoje. Comece pelo impacto no posicionamento dos investidores, não apenas pelo que aconteceu. Use uma analogia se ajudar, extraída da história financeira ou de precedentes de política monetária — não comparações com produtos do dia a dia. Termine com uma frase conectando essa história ao que vem a seguir no boletim.

SEÇÃO 3 — PANORAMA DAS AÇÕES (~300 palavras)
Comente 4 a 5 nomes entre as carteiras monitoradas. Para cada um: o que aconteceu, por quê, e o que isso sinaliza sobre o setor ou o tema macro mais amplo. Inclua as variações percentuais. Conecte cada movimento além da ação individual — se a ASML subiu, relacione aos ciclos de capex em IA; se uma ação brasileira se moveu, conecte à demanda por commodities ou à China. Termine com uma frase identificando o tema dominante entre os nomes comentados.

SEÇÃO 4 — APROFUNDAMENTO MACRO (~200 palavras)
Escolha a história macro mais importante nos dados de hoje — movimento de commodity, sinal de juros, divergência geográfica — e vá além do título. Aborde: o que o movimento implica sobre o que os investidores esperam atualmente, e o que mudaria esse quadro. Termine com uma frase sobre como essa história macro se conecta de volta às ações.

SEÇÃO 5 — CONECTANDO OS PONTOS (~150 palavras)
Sintetize em 2 temas de múltiplos mercados. Use linguagem conectiva: "o fio condutor aqui é...", "o que o mercado está sinalizando é...". Note divergências incomuns e o que elas historicamente indicam. Termine com uma frase que enquadre o caráter geral da sessão de hoje.

SEÇÃO 6 — A VISÃO DO ANALISTA (~150 palavras)
Estruture em quatro partes:
(a) Sua tese direcional para a próxima sessão ou semana, dita claramente.
(b) Um dado de apoio ou paralelo histórico com números.
(c) O que especificamente invalidaria essa visão.
(d) Uma implicação prática: o que um gestor faria com essa informação.
Tom: confiante, específico e honesto sobre a incerteza. Defenda uma visão.

SEÇÃO 7 — O QUE ACOMPANHAR (~80 palavras)
Feche com 2 catalisadores específicos nas próximas 24 a 48 horas. Nomeie eventos reais. Diga qual resultado seria positivo (bom para os mercados) ou negativo (ruim para os mercados) e por quê.

---

REGRAS DE ESTILO:
- Escreva em parágrafos falados corridos. Sem listas, sem marcadores, sem cabeçalhos no texto de saída — este boletim é lido em voz alta.
- Todo termo financeiro deve ser explicado em linguagem simples na mesma frase em que aparece pela primeira vez. NÃO use estas expressões sem explicação imediata:
  "correlação negativa entre ações e títulos" → diga "títulos subindo enquanto ações caem — o movimento clássico de proteção"
  "prêmio de risco geopolítico" → diga "a cautela extra que os investidores embutem nos preços quando as tensões políticas aumentam"
  "índices amplos de ações" → diga "os principais benchmarks do mercado, como o S&P 500"
  "o USO recuou" → diga "o petróleo devolveu [X]% dos ganhos do dia"
  "short squeeze" → diga "investidores que apostaram na queda sendo forçados a reverter suas posições rapidamente"
  "reprecificação fundamentalista" → diga "o mercado reavaliando o quanto uma empresa realmente vale"
- Quando couber naturalmente, use o raciocínio de Peter Lynch de 'One Up on Wall Street': essa empresa tem uma história simples de entender? Esse movimento é típico de uma empresa de crescimento rápido, estável, cíclica ou em virada? Helena consegue ver os produtos ou clientes desta empresa no cotidiano? Aplique apenas quando encaixar de forma natural — não force.
- Termine cada seção com uma frase de transição resumindo o principal ponto antes de avançar. Mantenha breve e natural para o áudio falado.
- Analogias devem vir da história financeira ou de precedentes de política monetária — não de produtos do dia a dia.
- Inclua números reais ao longo do texto: níveis de índices, variações percentuais, níveis de juros, variações de preço.
- Tom confiante, direto e profissional — como um analista experiente conversando com um estagiário motivado.
- Sem frases de enchimento: sem "vamos explorar", "aqui está a surpresa", "boa pergunta" ou "fascinante".
- Não use listas ou marcadores em nenhum ponto do texto de saída."""

    async with anthropic_client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=3000,
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
    intro = f"Helena Alpha Engine — Boletim Diário de Mercado — {date.today().strftime('%d de %B de %Y')}.\n\n"
    full_text = intro + narrative
    tts_chunks = _chunk_text_for_tts(full_text)
    audio_parts = []
    for chunk in tts_chunks:
        chunk_response = await openai_client.audio.speech.create(
            model="tts-1-hd",
            voice="nova",
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
