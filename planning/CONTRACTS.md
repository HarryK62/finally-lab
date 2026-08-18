# FinAlly — Integration Contract

**Status:** Authoritative. This document is the binding interface contract between agents.

`PLAN.md` defines *what* to build. This document pins *exact* shapes so agents can build in
parallel without integrating badly. If PLAN.md and this document disagree on a detail,
**this document wins**; if this document is silent, PLAN.md governs.

**Rule: never change a shape defined here unilaterally.** If you need a change, report it to the
orchestrator rather than editing this file yourself.

---

## 1. File Ownership

Each path has exactly one owning agent. **Do not create or edit files owned by another agent.**

| Path | Owner |
|---|---|
| `backend/app/market/**` | **NOBODY — frozen.** Complete and tested. Read-only for all agents. |
| `backend/app/config.py`, `runtime.py`, `main.py` | backend-engineer |
| `backend/app/db/**` | backend-engineer |
| `backend/app/schemas.py` | backend-engineer |
| `backend/app/services/portfolio.py`, `services/watchlist.py` | backend-engineer |
| `backend/app/api/health.py`, `api/portfolio.py`, `api/watchlist.py` | backend-engineer |
| `backend/tests/test_db.py`, `test_services_*.py`, `test_api_*.py` | backend-engineer |
| `backend/app/llm/**` | ai-engineer |
| `backend/app/api/chat.py` | ai-engineer |
| `backend/tests/test_llm_*.py`, `test_api_chat.py` | ai-engineer |
| `frontend/**` | frontend-engineer |
| `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `scripts/**`, `.env.example` | devops-engineer |
| `test/**` | qa-engineer |
| `backend/pyproject.toml` | backend-engineer owns; ai-engineer may **append** deps only |
| `planning/**`, `CLAUDE.md`, `README.md` | orchestrator |

---

## 2. Backend Module Layout

```
backend/app/
├── __init__.py
├── config.py          # env-driven settings
├── runtime.py         # process-wide singletons (price cache, market source)
├── main.py            # FastAPI app: lifespan, router registration, static mount
├── schemas.py         # ALL Pydantic request/response models
├── db/
│   ├── __init__.py
│   ├── schema.sql     # DDL, verbatim from §4
│   └── database.py    # connection management, lazy init, seeding
├── services/
│   ├── __init__.py
│   ├── portfolio.py
│   └── watchlist.py
├── api/
│   ├── __init__.py
│   ├── health.py
│   ├── portfolio.py
│   ├── watchlist.py
│   └── chat.py        # ai-engineer
├── llm/               # ai-engineer
│   ├── __init__.py
│   ├── client.py      # LiteLLM/OpenRouter/Cerebras call
│   ├── prompts.py     # system prompt + context builder
│   ├── schemas.py     # structured-output Pydantic models
│   └── mock.py        # LLM_MOCK=true deterministic responses
└── market/            # FROZEN
```

### `runtime.py` — the shared-state contract

Services and the chat flow need live prices without importing FastAPI request state.

```python
from app.market import PriceCache, MarketDataSource

def get_price_cache() -> PriceCache: ...
def get_market_source() -> MarketDataSource: ...   # raises RuntimeError if not started
def set_market_source(source: MarketDataSource) -> None: ...
```

`get_price_cache()` returns a module-level singleton `PriceCache`, created on first call.
It is safe to call at import time and in tests. `main.py` owns starting/stopping the source
in the FastAPI lifespan.

### `config.py`

Reads `.env` from the project root (use `python-dotenv`; the root is two levels above
`app/`, and must also work when CWD is `/app` in Docker).

| Setting | Env var | Default |
|---|---|---|
| `DB_PATH` | `DB_PATH` | `<repo_root>/db/finally.db` |
| `OPENROUTER_API_KEY` | `OPENROUTER_API_KEY` | `""` |
| `MASSIVE_API_KEY` | `MASSIVE_API_KEY` | `""` |
| `LLM_MOCK` | `LLM_MOCK` | `False` (true iff the value lowercases to `"true"`/`"1"`) |
| `STATIC_DIR` | `STATIC_DIR` | `<repo_root>/backend/static` |
| `DEFAULT_USER_ID` | — | `"default"` (constant) |
| `SNAPSHOT_INTERVAL_SECONDS` | — | `30` (constant) |

---

## 3. Conventions (all backend agents)

- **Money/quantities**: `float` throughout. Round only at the API boundary — prices and
  currency to **2 dp**, percentages to **2 dp**, quantities to **6 dp**.
- **Timestamps**: ISO-8601 UTC strings with a `Z` suffix, e.g. `2026-08-17T14:03:22.481Z`.
  Use one shared helper `app/db/database.py::utc_now_iso()`.
- **IDs**: `str(uuid.uuid4())`.
- **Tickers**: normalize to **uppercase, stripped** at every entry point (API, service, LLM).
- **Errors**: raise `fastapi.HTTPException(status_code, detail="human readable message")`.
  FastAPI renders `{"detail": "..."}`. Never invent an alternative error envelope.
- **SQLite**: `check_same_thread=False`, `row_factory = sqlite3.Row`, and
  `PRAGMA journal_mode=WAL` + `PRAGMA foreign_keys=ON` on connect. Because writes can arrive
  from the request path *and* the snapshot background task, guard all writes with a module-level
  `threading.Lock` and run blocking DB work off the event loop via `anyio.to_thread.run_sync`
  (or `asyncio.to_thread`). Never hold a connection open across an `await`.
- **Style**: `from __future__ import annotations`, full type hints, module docstrings,
  `ruff check` clean at line-length 100 (match the existing `app/market/` style closely).

---

## 4. Database Schema (verbatim)

```sql
CREATE TABLE IF NOT EXISTS users_profile (
    id           TEXT PRIMARY KEY DEFAULT 'default',
    cash_balance REAL NOT NULL DEFAULT 10000.0,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watchlist (
    id       TEXT PRIMARY KEY,
    user_id  TEXT NOT NULL DEFAULT 'default',
    ticker   TEXT NOT NULL,
    added_at TEXT NOT NULL,
    UNIQUE (user_id, ticker)
);

CREATE TABLE IF NOT EXISTS positions (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL DEFAULT 'default',
    ticker     TEXT NOT NULL,
    quantity   REAL NOT NULL,
    avg_cost   REAL NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (user_id, ticker)
);

CREATE TABLE IF NOT EXISTS trades (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT 'default',
    ticker      TEXT NOT NULL,
    side        TEXT NOT NULL CHECK (side IN ('buy','sell')),
    quantity    REAL NOT NULL,
    price       REAL NOT NULL,
    executed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT 'default',
    total_value REAL NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL DEFAULT 'default',
    role       TEXT NOT NULL CHECK (role IN ('user','assistant')),
    content    TEXT NOT NULL,
    actions    TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trades_user_time     ON trades (user_id, executed_at);
CREATE INDEX IF NOT EXISTS idx_snapshots_user_time  ON portfolio_snapshots (user_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_chat_user_time       ON chat_messages (user_id, created_at);
```

**Seed** (only when `users_profile` has no `default` row): one profile with
`cash_balance=10000.0`, plus watchlist rows for
`AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX` (import the list from
`app.market.seed_prices`, do not re-hardcode it).

---

## 5. Service Layer (backend-engineer)

`app/services/portfolio.py`:

```python
def get_portfolio(user_id: str = DEFAULT_USER_ID) -> PortfolioResponse
def execute_trade(ticker: str, quantity: float, side: str,
                  user_id: str = DEFAULT_USER_ID) -> TradeResponse
def get_history(limit: int = 500, user_id: str = DEFAULT_USER_ID) -> HistoryResponse
def record_snapshot(user_id: str = DEFAULT_USER_ID) -> None
```

`app/services/watchlist.py`:

```python
async def get_watchlist(user_id: str = DEFAULT_USER_ID) -> WatchlistResponse
async def add_ticker(ticker: str, user_id: str = DEFAULT_USER_ID) -> WatchlistItem
async def remove_ticker(ticker: str, user_id: str = DEFAULT_USER_ID) -> None
```

Watchlist functions are `async` because they must also call
`get_market_source().add_ticker(...)` / `.remove_ticker(...)` to keep the price feed in sync.
**The AI agent calls these same service functions — it must never re-implement trade or
watchlist logic, and must never write to the DB directly.**

### Trade rules (exact)

1. Normalize ticker; `quantity` must be `> 0` else `400 "Quantity must be greater than zero"`.
2. `side` must be `buy` or `sell` (case-insensitive) else `400`.
3. Price comes from `get_price_cache().get_price(ticker)`. If `None` →
   `400 "No price available for {TICKER}"`.
4. **Buy**: `cost = round(quantity * price, 2)`. If `cost > cash_balance` →
   `400 "Insufficient cash: need $X.XX, have $Y.YY"`.
   New `avg_cost = (old_qty*old_avg + quantity*price) / (old_qty + quantity)`.
   Decrement cash by `cost`.
5. **Sell**: if `quantity > held_quantity` (tolerance `1e-9`) →
   `400 "Insufficient shares: trying to sell X, hold Y"`. `avg_cost` is unchanged on a sell.
   Increment cash by `round(quantity * price, 2)`.
   If the resulting quantity is `<= 1e-9`, **delete the position row**.
6. Insert the `trades` row, then call `record_snapshot()`. All of steps 4–6 happen inside a
   single SQLite transaction (snapshot may be a follow-up write).
7. A trade never auto-adds the ticker to the watchlist.

---

## 6. HTTP API (exact shapes)

All responses are JSON. All errors are `{"detail": "..."}` with the listed status code.

### `GET /api/health` → 200
```json
{"status": "ok", "market_source": "simulator", "llm_mock": false}
```
`market_source` is `"simulator"` or `"massive"`.

### `GET /api/portfolio` → 200
```json
{
  "cash_balance": 8050.00,
  "positions": [
    {"ticker": "AAPL", "quantity": 10.0, "avg_cost": 190.00, "current_price": 195.00,
     "market_value": 1950.00, "cost_basis": 1900.00,
     "unrealized_pnl": 50.00, "unrealized_pnl_percent": 2.63, "weight": 0.1631}
  ],
  "positions_value": 1950.00,
  "total_value": 10000.00,
  "total_cost_basis": 1900.00,
  "total_unrealized_pnl": 50.00,
  "total_unrealized_pnl_percent": 2.63
}
```
- `weight` = `market_value / positions_value` (0.0 when `positions_value == 0`), 4 dp.
- `total_value` = `cash_balance + positions_value`.
- `total_unrealized_pnl_percent` is relative to `total_cost_basis` (0.0 when it is 0).
- If a position has no cached price, use `avg_cost` as `current_price` (P&L then reads 0).
- `positions` is sorted by `market_value` descending.

### `POST /api/portfolio/trade` → 200
Request: `{"ticker": "AAPL", "quantity": 10, "side": "buy"}`
```json
{
  "trade": {"id": "uuid", "ticker": "AAPL", "side": "buy", "quantity": 10.0,
            "price": 195.00, "total": 1950.00, "executed_at": "2026-08-17T14:03:22.481Z"},
  "cash_balance": 8050.00,
  "position": {"ticker": "AAPL", "quantity": 10.0, "avg_cost": 195.00, ...},
  "total_value": 10000.00
}
```
`position` is the full position object as in `GET /api/portfolio`, or `null` if the position was
closed out by a sell.

### `GET /api/portfolio/history?limit=500` → 200
```json
{"snapshots": [{"total_value": 10000.00, "recorded_at": "2026-08-17T14:00:00.000Z"}]}
```
Ascending by `recorded_at` (oldest first — chart-ready). `limit` returns the **most recent**
`limit` snapshots, still ascending. Default 500, max 5000.

### `GET /api/watchlist` → 200
```json
{"tickers": [
  {"ticker": "AAPL", "added_at": "2026-08-17T14:00:00.000Z", "price": 195.00,
   "previous_price": 194.50, "change": 0.50, "change_percent": 0.26, "direction": "up"}
]}
```
Sorted by `added_at` ascending. If no price is cached yet, `price`/`previous_price` are `null`,
`change`/`change_percent` are `0.0`, `direction` is `"flat"`.

### `POST /api/watchlist` → 201
Request `{"ticker": "pypl"}` → the single `WatchlistItem` object (same shape as above).
- Already present → `409 "PYPL is already in the watchlist"`
- Invalid symbol (not 1–8 chars of `A–Z`) → `400 "Invalid ticker symbol"`

### `DELETE /api/watchlist/{ticker}` → 204, empty body
- Not present → `404 "PYPL is not in the watchlist"`

### `GET /api/stream/prices` — SSE, already implemented (frozen)
Each event's `data:` is an object keyed by ticker:
```json
{"AAPL": {"ticker":"AAPL","price":195.0,"previous_price":194.5,"timestamp":1755439402.48,
          "change":0.5,"change_percent":0.26,"direction":"up"}}
```
Note `timestamp` here is **Unix seconds (float)**, not ISO — this endpoint is frozen, so the
frontend adapts to it. A `retry: 1000` directive is sent first.

### `POST /api/chat` → 200 (ai-engineer)
Request: `{"message": "buy 5 tesla"}`
```json
{
  "id": "uuid",
  "message": "Bought 5 shares of TSLA at $412.30. That's 20.6% of your portfolio...",
  "actions": [
    {"type": "trade", "status": "executed", "ticker": "TSLA", "side": "buy",
     "quantity": 5.0, "price": 412.30, "detail": "Bought 5 TSLA @ $412.30"},
    {"type": "watchlist", "status": "failed", "ticker": "ZZZZ", "action": "add",
     "detail": "No price available for ZZZZ"}
  ],
  "created_at": "2026-08-17T14:03:22.481Z"
}
```
- `actions` is always an array (empty when nothing was executed).
- `type` ∈ `{"trade","watchlist"}`; `status` ∈ `{"executed","failed"}`.
- Trade actions carry `ticker`/`side`/`quantity`/`price` (`price` `null` when failed);
  watchlist actions carry `ticker`/`action` (`"add"`/`"remove"`).
- `detail` is always a human-readable string. Failures never 500 — a failed action is a
  successful response containing a `failed` action, and the failure reason is fed back into the
  assistant's `message`.
- Upstream LLM/network failure → `503 "AI assistant is unavailable: {reason}"`.
- Empty/whitespace message → `400 "Message cannot be empty"`.

### `GET /api/chat/history?limit=50` → 200 (ai-engineer)
```json
{"messages": [{"id":"uuid","role":"user","content":"...","actions":null,
               "created_at":"..."}]}
```
Ascending by `created_at` (oldest first). `actions` is the parsed array for assistant messages,
`null` for user messages.

---

## 7. LLM Contract (ai-engineer)

Use the **cerebras** skill: LiteLLM → OpenRouter → `openrouter/openai/gpt-oss-120b` with
`extra_body={"provider": {"order": ["cerebras"]}}` and `reasoning_effort="low"`.
Add `litellm` and `pydantic` to `backend/pyproject.toml` dependencies.

Structured output model:

```python
class Trade(BaseModel):
    ticker: str
    side: Literal["buy", "sell"]
    quantity: float

class WatchlistChange(BaseModel):
    ticker: str
    action: Literal["add", "remove"]

class AssistantResponse(BaseModel):
    message: str
    trades: list[Trade] = []
    watchlist_changes: list[WatchlistChange] = []
```

Flow per `POST /api/chat`:
1. Persist the user message to `chat_messages`.
2. Build context: cash, positions with live P&L, watchlist with live prices, total value —
   sourced from `services.portfolio.get_portfolio()` and `services.watchlist.get_watchlist()`.
3. Load the last **20** messages of history.
4. Call the LLM (or `mock.py` when `LLM_MOCK`), parse into `AssistantResponse`.
5. Execute each trade via `services.portfolio.execute_trade` and each watchlist change via
   `services.watchlist.add_ticker`/`remove_ticker`, catching `HTTPException` per action and
   recording `status="failed"` with `exc.detail` as `detail`.
6. If any action failed, append a short plain-language note about the failure(s) to `message`.
7. Persist the assistant message with `actions` as a JSON string; return the response.

`LLM_MOCK=true` must be deterministic, need no API key, and must be keyword-driven so E2E can
assert on it:

| User message contains (case-insensitive) | Mock behaviour |
|---|---|
| `buy <n> <TICKER>` | one buy trade for that ticker/qty |
| `sell <n> <TICKER>` | one sell trade |
| `add <TICKER>` / `watch <TICKER>` | watchlist add |
| `remove <TICKER>` | watchlist remove |
| anything else | message only, no actions; text mentions cash balance and position count |

Mock messages must start with a stable prefix `"[mock]"` so tests can assert on it.

---

## 8. Frontend Contract (frontend-engineer)

- Next.js + TypeScript, `output: 'export'`, `images: {unoptimized: true}`, Tailwind, dark theme.
- Build output goes to `frontend/out`; the Dockerfile copies it to `backend/static`.
- **All** network calls are same-origin relative paths (`/api/...`). Never hardcode a host or port.
- For local dev against a running backend on `:8000`, use a `next.config` rewrite or
  `NEXT_PUBLIC_API_BASE` defaulting to `""` — but the production build must emit relative paths.
- SSE via native `EventSource('/api/stream/prices')`. Each message is the full ticker map
  from §6; merge it into state and keep a client-side ring buffer (last 120 points per ticker)
  for sparklines.
- Connection status dot: `green` = open, `yellow` = `EventSource` reconnecting
  (`readyState === 0` after having been open), `red` = errored/closed.
- Colors: accent `#ecad0a`, primary `#209dd7`, secondary `#753991` (submit buttons),
  background `#0d1117`. Up = green, down = red, with a ~500ms fading flash on change.
- Charting: Recharts or Lightweight Charts. Portfolio heatmap is a treemap sized by `weight`,
  colored by `unrealized_pnl_percent`.
- After any successful trade or watchlist mutation, refetch `/api/portfolio` and
  `/api/watchlist`. Poll `/api/portfolio` every 5s so P&L tracks live prices.

---

## 9. Docker / Runtime Contract (devops-engineer)

- Multi-stage: `node:20-slim` builds `frontend/` → stage 2 `python:3.12-slim` with `uv`.
- Frontend `out/` is copied to `/app/backend/static`; `STATIC_DIR` defaults there.
- App runs `uvicorn app.main:app --host 0.0.0.0 --port 8000` from `/app/backend`.
- SQLite lives at `/app/db/finally.db` (`DB_PATH`), volume `finally-data:/app/db`.
- `main.py` mounts static **last**, after all `/api` routers, and serves `index.html` for
  unknown non-`/api` paths (SPA fallback) with a 404 for unknown `/api` paths.
- `.env.example` documents `OPENROUTER_API_KEY`, `MASSIVE_API_KEY`, `LLM_MOCK`.

## 10. Local Dev Commands (no Docker required)

```bash
cd backend && uv run --extra dev pytest -q          # backend tests
cd backend && uv run uvicorn app.main:app --reload  # backend on :8000
cd frontend && npm run dev                          # frontend on :3000 (proxies /api → :8000)
cd frontend && npm run build                        # static export → frontend/out
```

**Both run paths are supported; check `docker info` before relying on Docker.** Availability has
varied between machines (a daemon was reachable on 2026-08-17, absent on 2026-08-18), so treat it
as something to verify rather than assume. When the daemon is up, build and run the container for
real rather than validating by inspection; when it is not, use the local uvicorn path below.

```bash
docker compose up -d --build && curl http://localhost:8000/api/health
```

Both run paths are supported and either is acceptable for E2E: a locally-started uvicorn plus
static export, or docker-compose. If you use the container, note that SQLite persists in the
named volume `finally-data` between runs — use a throwaway volume or a per-run `DB_PATH` override
so fresh-start scenarios are honest, and never delete the developer's `db/finally.db`.
