# FinAlly — AI Trading Workstation

A visually stunning AI-powered trading workstation that streams live market data, simulates portfolio trading, and integrates an LLM chat assistant that can analyze positions and execute trades via natural language.

Built entirely by coding agents as a capstone project for an agentic AI coding course.

## Features

- **Live price streaming** via SSE with green/red flash animations
- **Simulated portfolio** — $10k virtual cash, market orders, instant fills
- **Portfolio visualizations** — heatmap (treemap), P&L chart, positions table
- **AI chat assistant** — analyzes holdings, suggests and auto-executes trades
- **Watchlist management** — track tickers manually or via AI
- **Dark terminal aesthetic** — Bloomberg-inspired, data-dense layout

## Architecture

Single Docker container serving everything on port 8000:

- **Frontend**: Next.js (static export) with TypeScript and Tailwind CSS
- **Backend**: FastAPI (Python/uv) with SSE streaming
- **Database**: SQLite with lazy initialization
- **AI**: LiteLLM → OpenRouter (Cerebras inference) with structured outputs
- **Market data**: Built-in GBM simulator (default) or Massive API (optional)

## Quick Start

```bash
# Clone and configure
cp .env.example .env
# Optional: add your OPENROUTER_API_KEY for the AI chat panel

# Run with Docker
docker build -t finally .
docker run -v finally-data:/app/db -p 8000:8000 --env-file .env finally

# Open http://localhost:8000
```

## Environment Variables

Every variable is optional — with an empty `.env` the app still runs on the built-in
simulator, and the chat panel returns a clean "not configured" error.

| Variable | Required | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | No — needed only for live AI chat | OpenRouter API key; without it `/api/chat` returns 503 |
| `MASSIVE_API_KEY` | No | Massive (Polygon.io) key for real market data; omit to use simulator |
| `LLM_MOCK` | No | Set `true` for deterministic mock LLM responses (testing) |

## Local development (without Docker)

```bash
# Backend — API on http://localhost:8000
cd backend && uv run uvicorn app.main:app --reload

# Frontend — static export into backend/static
cd frontend && npm ci && npm run build

# Test suites
cd backend  && uv run --extra dev pytest -q      # unit + API
cd frontend && npx vitest run                    # component + lib
cd test     && npm ci && npx playwright install --with-deps && npm test   # E2E
```

## Market data modes

Without `MASSIVE_API_KEY` the backend runs its built-in GBM simulator, which will
quote *any* symbol matching 1–8 A–Z characters — a typo like `APPL` is accepted and
priced. With a key it polls the real Massive snapshot API, where only genuine
symbols return data. Symbols that work in simulator mode may therefore go unpriced
in Massive mode.

Massive mode needs a plan entitled to the **real-time snapshot** endpoint
(`/v2/snapshot/locale/us/markets/stocks/tickers`). Plans limited to aggregates
return `403 NOT_AUTHORIZED` for every poll, which leaves all prices `null` and
makes nothing tradeable — `execute_trade` rejects any order without a cached
price. The simulator is the recommended default.

## Security

There is **no authentication** — anyone who can reach the port can trade and can spend
your OpenRouter credit through `/api/chat`. The container binds `0.0.0.0`, so keep it on
`localhost` or behind your own auth layer; do not expose a deployment publicly as-is.

## Project Structure

```
finally/
├── frontend/    # Next.js static export
├── backend/     # FastAPI uv project
├── planning/    # Project documentation and agent contracts
├── test/        # Playwright E2E tests
├── db/          # SQLite volume mount (runtime)
└── scripts/     # Start/stop helpers
```

## License

See [LICENSE](LICENSE).
