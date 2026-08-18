# FinAlly — Full-Stack Code Review

**Date:** 2026-08-18
**Reviewed commit:** `34b11b2` (main, working tree clean)
**Scope:** the whole delivered platform — `backend/` (market data, services, API, LLM chat),
`frontend/`, `test/` (Playwright E2E), and the packaging layer (Dockerfile, compose, scripts).
Reference documents: `planning/PLAN.md`, `planning/CONTRACTS.md`,
`planning/MARKET_DATA_SUMMARY.md` and the five documents in `planning/archive/`.

> Naming note: this file is named `MARKET_DATA_REVIEW.md` as requested, but unlike
> `planning/archive/MARKET_DATA_REVIEW.md` (which covered only `backend/app/market/`) it
> reviews the entire application. All seven issues raised by that earlier market-data review
> have been fixed and verified fixed here.

---

## 1. Verdict

The build is in good shape and matches its own contract closely. All three test suites pass,
lint is clean, the static export builds, and the E2E suite drives the real stack end to end.
The code reads like it was written by one careful author rather than six agents: consistent
module docstrings, consistent rounding discipline, and comments that explain *why* rather than
*what*.

Four issues are worth fixing before this is shown off. Only one of them is a genuine behavioural
bug (§4.1: a held position becomes unsellable after a restart if it is not on the watchlist);
the rest are robustness and developer-experience papercuts.

Two things could not be exercised in this environment and remain unverified: **the Docker image**
(no Docker daemon on this machine) and **the live LLM path** (no `.env`, no `OPENROUTER_API_KEY`).

---

## 2. Test results

Everything below was actually run, in this environment, on the reviewed commit.

| Suite | Command | Result |
|---|---|---|
| Backend unit/API | `uv run --extra dev pytest -q --cov=app` | **226 passed**, 0 failed, 9.7s |
| Backend lint | `uv run --extra dev ruff check .` | **All checks passed** |
| Backend lockfile | `uv lock --check` | up to date (`uv sync --frozen` in the Dockerfile will work) |
| Frontend unit | `npx vitest run` | **202 passed** across 18 files, 7.9s |
| Frontend lint | `npm run lint` (eslint) | clean |
| Frontend build | `npm run build` | static export OK (`out/index.html` produced, 4 pages) |
| Frontend typecheck | `npm run typecheck` (`tsc --noEmit`) | **fails on a clean checkout**, passes after a build — see §4.3 |
| E2E | `cd test && npm test` | **30 passed**, 40.3s, 0 failed |
| Docker image | `docker build` / `docker compose up` | **not run** — no Docker daemon in this environment |
| Live LLM call | real OpenRouter/Cerebras completion | **not run** — no API key configured |

### Backend coverage — 96% overall (1103 statements, 48 missed)

| Module group | Coverage | Notes |
|---|---|---|
| `app/services/*`, `app/schemas.py`, `app/api/portfolio.py`, `app/api/watchlist.py` | 99–100% | |
| `app/api/chat.py`, `app/llm/*` | 95–100% | mock and error paths both covered |
| `app/db/database.py` | 95% | uncovered: the `ensure_db` double-checked-lock branch |
| `app/market/*` | 94–100% except `stream.py` | `stream.py` at 42% (the SSE generator is only covered by E2E) |

The earlier market-data review reported 84% with 5 failing tests; both problems are resolved.
`massive` is now a top-level import, so the `test_massive.py` patches bind correctly.

### E2E notes

The suite runs the "local uvicorn + static export" path from `test/playwright.config.ts`, with a
throwaway `DB_PATH` per run and `LLM_MOCK=true`. It required installing Playwright's system
libraries (`libglib-2.0` and friends were absent from this VM) — that is an environment gap, not
a suite defect. All eight specs pass: fresh start, watchlist CRUD, buy/partial-sell/close-out,
rejected orders, heatmap + P&L chart, mocked AI chat including a *failed* action, SSE
disconnect/reconnect, and SPA routing vs. JSON 404s.

---

## 3. Architecture assessment

Layering is clean and the contract's ownership boundaries held up:

```
EventSource ──► /api/stream/prices ──► PriceCache ◄── SimulatorDataSource | MassiveDataSource
                                          ▲
   REST /api/portfolio, /api/watchlist ───┤
                    /api/chat ─► llm/client ─► services.portfolio / services.watchlist ─► SQLite
```

What is done particularly well:

- **The AI never re-implements business logic.** `api/chat.py` calls `services.portfolio.execute_trade`
  and the watchlist services, so an LLM-driven trade goes through exactly the validation a manual
  one does. Failed actions become `status="failed"` entries plus a plain-language note appended to
  the reply, and still return 200 — as `CONTRACTS.md` §6 requires, and as spec 06 asserts.
- **Rounding discipline.** `api_round()` (`services/portfolio.py:58`) rounds at the API boundary
  *and* collapses `-0.0`, which is the kind of detail that usually leaks "-0.00%" into a demo.
- **Live re-pricing on the client.** `lib/portfolio.ts` recomputes market value, P&L and weight
  from the SSE stream using the same formulas as the server, so the header tracks prices at 2 Hz
  while `/api/portfolio` is polled only every 5s. The formulas are duplicated deliberately and the
  comment says so.
- **Identity-preserving merges.** `mergeTicks`/`appendTicks` return the previous object when nothing
  moved, so ten watchlist rows do not re-render twice a second.
- **Honest SSE status semantics.** `usePriceStream` distinguishes "EventSource is retrying by itself"
  (yellow) from CLOSED, which the browser never retries, and rebuilds the source with capped
  exponential backoff. That second case is the one most implementations miss.
- **Transaction and threading hygiene.** One `threading.Lock` around every write, no connection held
  across an `await`, all blocking DB work dispatched with `anyio.to_thread.run_sync`.
- **The test suites are assertive, not decorative.** The backend tests pin exact error strings from
  the contract; the E2E specs assert deltas rather than absolute balances (correct, since the
  simulator's price at fill time is unknowable) and check the heatmap ramp as a *relation* to the
  neutral midpoint rather than a fixed colour.

---

## 4. Findings

### 4.1 A held position loses its price feed across a restart — Medium

`app/services/watchlist.py:144` deliberately keeps a ticker on the live feed when the user removes
it from the watchlist but still holds shares, because `execute_trade` rejects any order without a
cached price. That protection exists only in the running process. `app/main.py:61` starts the market
data source from `get_watchlist_tickers()` alone, so after a container restart the held-but-unwatched
ticker is never streamed.

Verified empirically (fresh DB, 5 AAPL held, AAPL removed from the watchlist, app restarted):

```
current_price: 190.0   avg_cost: 190.0     # P&L frozen at cost, forever
cached price : None
SELL REJECTED: 400 No price available for AAPL
```

The position becomes permanently unsellable and its P&L reads 0 — the exact failure the runtime
code goes out of its way to prevent, and the data survives restarts in the named volume, so it does
not heal.

**Fix:** start the feed on the union of watchlist tickers and held position tickers.

```python
# app/main.py, in lifespan()
tickers = await to_thread.run_sync(get_startup_tickers)   # watchlist ∪ held positions
```

A `SELECT ticker FROM positions WHERE user_id = ? AND quantity > 1e-9` unioned into
`get_watchlist_tickers` is enough. Worth a regression test alongside the existing
`test_services_watchlist.py` held-position tests.

### 4.2 A non-finite quantity reaches SQLite and returns a 500 — Low

Python's `json` module (which Starlette uses) accepts the non-standard `NaN` / `Infinity` literals,
and Pydantic's `float` accepts them. `execute_trade` guards `quantity <= 0` (`services/portfolio.py:207`),
which is `False` for `NaN`, so a NaN quantity flows through the arithmetic — `total`, `new_quantity`
and `new_avg_cost` all become NaN — until SQLite converts NaN to NULL and the `NOT NULL` constraint
trips.

Verified: `POST /api/portfolio/trade` with body `{"ticker":"AAPL","quantity":NaN,"side":"buy"}`
raises `sqlite3.IntegrityError` → HTTP 500 with a traceback in the logs.

The damage is contained — the transaction rolls back, cash and positions are untouched — so this is
a hygiene issue, not a corruption one. The database's `NOT NULL` constraint is doing the work that
validation should.

**Fix:** one line in the quantity guard:

```python
if not math.isfinite(quantity) or quantity <= 0:
    raise HTTPException(status_code=400, detail="Quantity must be greater than zero")
```

(Or `Field(gt=0, allow_inf_nan=False)` on `TradeRequest.quantity`, which also fixes it for the HTTP
path but not for the LLM path, which calls the service directly.)

### 4.3 `npm run typecheck` fails on a fresh clone — Low

```
src/app/layout.tsx(25,50): error TS2304: Cannot find name 'LayoutProps'.
```

`LayoutProps<"/">` is a Next 16 generated global that lives in `.next/types`, which only exists after
a `next build` or `next dev`. `tsconfig.json` includes `.next/types/**/*.ts`, so the check passes
after a build and fails before one. Anyone running the documented command first — a reviewer, CI —
sees a red error on an otherwise healthy repo.

**Fix:** either `"typecheck": "next typegen && tsc --noEmit"`, or type the layout props explicitly
(`{ children }: { children: React.ReactNode }`). Nothing else in the tree depends on the generated
globals.

### 4.4 A held-but-unwatched ticker can be selected but never charts — Low

`state/terminal.tsx:186` prunes sparkline buffers down to the watchlist. But both `PositionsTable`
and `PortfolioHeatmap` call `select(position.ticker)`, and a position can legitimately be off the
watchlist (§4.1's scenario, while the process is still up). Selecting one leaves `MainChart` on
"Accumulating {TICKER} price history from the live stream…" indefinitely, even though prices for
that ticker *are* streaming — its buffer is discarded on every render.

**Fix:** prune against watchlist tickers ∪ position tickers.

### 4.5 Smaller observations

- **`PriceCache.version` reads without the lock** (`app/market/cache.py:64`). Carried over from the
  earlier review, still open; harmless under the GIL, a real race on a free-threaded build. The module
  is frozen, so this is a note rather than a request.
- **`create_stream_router` mutates a module-level router** (`app/market/stream.py:17`), so calling it
  twice double-registers `/prices`. `main.py:122-130` memoises it specifically to avoid that — the
  workaround is correct, but the footgun is still in the frozen module.
- **`portfolio_snapshots` grows without bound** — one row every 30s (~2,880/day) plus one per trade,
  in a volume that persists. Nothing prunes or downsamples. Harmless for a course demo, worth a line
  of housekeeping if this ever runs for weeks.
- **The SSE stream has no heartbeat.** `_generate_events` only yields when the cache version changes.
  If the producer stalls, the connection sits silent and an intermediary proxy may drop it on an idle
  timeout. A comment-only keepalive (`: ping\n\n`) every ~15s would cover that; direct-to-uvicorn
  deployments are unaffected.
- **The simulator invents a price for any symbol.** `_add_ticker_internal` falls back to
  `random.uniform(50, 300)` for unknown tickers, so a typo like `APPL` is accepted by the 1–8 `A–Z`
  validator, quoted at a fabricated price, and tradeable. That is a reasonable demo choice — it is what
  makes "add PYPL" work — but it means the simulator and Massive modes disagree about which symbols
  exist, and the README does not say so.
- **`chat_messages` keeps orphaned user turns.** On a 503 from the model, the user's message has
  already been persisted (contract §7 step 1) with no reply after it. Correct per contract; slightly
  odd on reload, where the transcript ends with an unanswered question.
- **No authentication, by design.** Fine on `localhost`, but the container binds `0.0.0.0` and
  `PLAN.md` §11 floats App Runner/Render. Anyone who reaches a public deployment can trade, and can
  spend the owner's OpenRouter credit through `/api/chat`. Worth one explicit sentence in the README
  rather than leaving it implied.

---

## 5. Contract conformance

`planning/CONTRACTS.md` was checked section by section. No drift found in the shapes:

| Area | Status |
|---|---|
| §2 module layout, `runtime.py` / `config.py` surface | Matches, including the `DB_PATH`/`STATIC_DIR` defaults and `LLM_MOCK` parsing |
| §3 conventions (rounding, ISO-8601 `Z`, UUIDs, uppercase tickers, `{"detail": ...}`, WAL + lock) | Matches |
| §4 schema | **Byte-for-byte identical** to the contract, indexes included; seed list imported from `app.market.seed_prices` rather than re-hardcoded, as instructed |
| §5 trade rules 1–7 | All seven implemented, error strings verbatim; each has a test pinning the exact message |
| §6 HTTP shapes and status codes | Match, including 201 on add, 204 empty body on delete, 409/404 wording, ascending history with a most-recent `limit`, and `weight` at 4 dp |
| §7 LLM contract | `openrouter/openai/gpt-oss-120b`, `extra_body={"provider": {"order": ["cerebras"]}}`, `reasoning_effort="low"`, structured output — exactly the `cerebras` skill's snippet. Mock keyword table and `[mock]` prefix all present |
| §8 frontend contract | Relative paths only, 120-point ring buffer, three-state connection dot, contract colours in `globals.css`, refetch after mutations, 5s portfolio poll |
| §9 Docker/runtime | Multi-stage build as specified, static mounted last with SPA fallback and JSON 404s under `/api` (spec 08 proves both) |

One doc-vs-reality drift: **§10 asserts "Docker IS available in this environment (verified 2026-08-17)"**.
It is not available now — `docker` is not on `PATH` on this machine. That line has flip-flopped once
already; it would be safer as "either path is supported; check `docker info` before relying on it."

### Documentation drift

- `README.md:44` marks `OPENROUTER_API_KEY` as **Required: Yes**. It is not — the app runs fine
  without it (simulator prices, and chat returns a clean 503 or works under `LLM_MOCK=true`).
- The README documents only the Docker path. The no-Docker path that this review actually used
  (`uv run uvicorn`, `npm run build`, `cd test && npm test`) is documented in `CONTRACTS.md` §10 but
  not anywhere a user would look.
- `Read-Anal.md` at the repo root is a README review from before the build ("only `backend/` and
  `planning/` exist"). Every finding in it is now stale. It should be archived or deleted so nobody
  reads it as current.

---

## 6. Recommended order of work

1. **§4.1** — start the price feed from watchlist ∪ held positions (`app/main.py`), with a regression test.
2. **§4.3** — make `npm run typecheck` pass on a clean checkout.
3. **§4.2** — reject non-finite quantities in `execute_trade`.
4. **§4.4** — prune sparkline buffers against positions as well as the watchlist.
5. **Docs** — fix the `OPENROUTER_API_KEY` "Required" row, add the local-dev commands, add one line
   about public exposure, retire `Read-Anal.md`, soften `CONTRACTS.md` §10.
6. **Unverified paths** — on a machine with Docker, run `docker compose up -d --build` plus
   `docker compose -f test/docker-compose.test.yml up --build --abort-on-container-exit`; with a real
   `OPENROUTER_API_KEY`, exercise one live chat turn including a trade. Neither could be checked here.
