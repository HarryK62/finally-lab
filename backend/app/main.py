"""FastAPI application: lifespan, router registration and static file serving.

Startup order matters:

1. Initialize and seed the SQLite database.
2. Create the market data source (simulator or Massive) and start it on the
   watched tickers plus any still-held positions.
3. Launch the background task that snapshots portfolio value every 30 seconds.

Static files are mounted **last**, after every ``/api`` router, so unknown
non-``/api`` paths fall through to ``index.html`` (SPA routing) while unknown
``/api`` paths still return 404.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import PurePath

from anyio import to_thread
from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

from app.api import chat, health, portfolio, watchlist
from app.config import SNAPSHOT_INTERVAL_SECONDS, get_settings
from app.db.database import init_db
from app.market import create_market_data_source, create_stream_router
from app.runtime import get_price_cache, set_market_source
from app.services.portfolio import record_snapshot
from app.services.watchlist import get_startup_tickers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _snapshot_loop() -> None:
    """Record a portfolio value snapshot every ``SNAPSHOT_INTERVAL_SECONDS``."""
    while True:
        await asyncio.sleep(SNAPSHOT_INTERVAL_SECONDS)
        try:
            await to_thread.run_sync(record_snapshot)
        except Exception:
            logger.exception("Portfolio snapshot failed")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Bring the database, market data feed and snapshot task up and down."""
    await to_thread.run_sync(init_db)

    source = create_market_data_source(get_price_cache())
    tickers = await to_thread.run_sync(get_startup_tickers)
    await source.start(tickers)
    set_market_source(source)

    # One snapshot at boot so the P&L chart is never empty on a fresh database.
    await to_thread.run_sync(record_snapshot)

    snapshot_task = asyncio.create_task(_snapshot_loop(), name="portfolio-snapshots")
    logger.info("FinAlly backend ready with %d tickers", len(tickers))

    try:
        yield
    finally:
        snapshot_task.cancel()
        try:
            await snapshot_task
        except asyncio.CancelledError:
            pass
        await source.stop()
        set_market_source(None)
        logger.info("FinAlly backend shut down")


class SPAStaticFiles(StaticFiles):
    """Static files with a single-page-app fallback to ``index.html``.

    A miss can surface two ways: ``StaticFiles`` raises 404, but in ``html=True``
    mode it first looks for ``404.html`` — which the Next.js export ships — and
    returns that as an ordinary 404 response. Both have to trigger the fallback.

    Unknown ``/api`` paths keep a plain 404 so API errors are never masked by HTML.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        if PurePath(path).parts[:1] == ("api",):
            raise StarletteHTTPException(status_code=404)
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
        else:
            if response.status_code != 404:
                return response
        return await super().get_response("index.html", scope)


def _mount_static(app: FastAPI) -> None:
    """Mount the frontend export if it exists; warn and carry on if it does not."""
    static_dir = get_settings().STATIC_DIR
    if not static_dir.is_dir():
        logger.warning(
            "Static directory %s does not exist - serving API only "
            "(build the frontend to populate it)",
            static_dir,
        )
        return
    app.mount("/", SPAStaticFiles(directory=static_dir, html=True), name="static")
    logger.info("Serving static frontend from %s", static_dir)


_stream_router: APIRouter | None = None


def _get_stream_router() -> APIRouter:
    """Build the SSE router once — ``create_stream_router`` mutates a shared router."""
    global _stream_router
    if _stream_router is None:
        _stream_router = create_stream_router(get_price_cache())
    return _stream_router


def create_app() -> FastAPI:
    """Build the FastAPI application with all routers and the static mount."""
    app = FastAPI(
        title="FinAlly",
        description="AI Trading Workstation",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.include_router(health.router)
    app.include_router(portfolio.router)
    app.include_router(watchlist.router)
    app.include_router(_get_stream_router())
    app.include_router(chat.router)

    # Static mount must stay last so it never shadows an /api route.
    _mount_static(app)
    return app


app = create_app()
