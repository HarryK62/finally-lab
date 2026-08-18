"""Watchlist management, kept in sync with the live market data source.

These functions are ``async`` because adding or removing a ticker must also tell
the running :class:`~app.market.MarketDataSource` to start or stop producing
prices for it — otherwise a newly watched ticker would never stream.
"""

from __future__ import annotations

import logging
import re
import sqlite3

from anyio import to_thread
from fastapi import HTTPException

from app.config import DEFAULT_USER_ID
from app.db.database import ensure_db, get_connection, new_id, utc_now_iso, write_connection
from app.runtime import get_market_source, get_price_cache
from app.schemas import WatchlistItem, WatchlistResponse
from app.services.portfolio import api_round, get_held_tickers, has_position, normalize_ticker

logger = logging.getLogger(__name__)

TICKER_PATTERN = re.compile(r"^[A-Z]{1,8}$")


# --- Helpers ---


def _validate_ticker(ticker: str) -> str:
    """Normalize and validate a symbol, raising 400 if it is not 1-8 A-Z chars."""
    ticker = normalize_ticker(ticker)
    if not TICKER_PATTERN.match(ticker):
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")
    return ticker


def _build_item(ticker: str, added_at: str) -> WatchlistItem:
    """Attach the latest cached price to a watchlist row."""
    update = get_price_cache().get(ticker)
    if update is None:
        return WatchlistItem(
            ticker=ticker,
            added_at=added_at,
            price=None,
            previous_price=None,
            change=0.0,
            change_percent=0.0,
            direction="flat",
        )
    return WatchlistItem(
        ticker=ticker,
        added_at=added_at,
        price=api_round(update.price, 2),
        previous_price=api_round(update.previous_price, 2),
        change=api_round(update.change, 2),
        change_percent=api_round(update.change_percent, 2),
        direction=update.direction,
    )


def _select_rows(user_id: str) -> list[sqlite3.Row]:
    """All watchlist rows for a user, oldest first.

    The ten seed rows share one ``added_at`` value, so ``rowid`` breaks the tie and
    keeps the default watchlist in its seeded order instead of an arbitrary one.
    """
    ensure_db()
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT ticker, added_at FROM watchlist
            WHERE user_id = ?
            ORDER BY added_at ASC, rowid ASC
            """,
            (user_id,),
        ).fetchall()


def _insert_row(ticker: str, user_id: str) -> str:
    """Insert a watchlist row, raising 409 if the ticker is already watched."""
    ensure_db()
    added_at = utc_now_iso()
    with write_connection() as conn:
        existing = conn.execute(
            "SELECT 1 FROM watchlist WHERE user_id = ? AND ticker = ?", (user_id, ticker)
        ).fetchone()
        if existing is not None:
            raise HTTPException(status_code=409, detail=f"{ticker} is already in the watchlist")
        conn.execute(
            "INSERT INTO watchlist (id, user_id, ticker, added_at) VALUES (?, ?, ?, ?)",
            (new_id(), user_id, ticker, added_at),
        )
    return added_at


def _delete_row(ticker: str, user_id: str) -> None:
    """Delete a watchlist row, raising 404 if the ticker is not watched."""
    ensure_db()
    with write_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM watchlist WHERE user_id = ? AND ticker = ?", (user_id, ticker)
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"{ticker} is not in the watchlist")


def get_watchlist_tickers(user_id: str = DEFAULT_USER_ID) -> list[str]:
    """Blocking helper: the watched tickers, oldest first."""
    return [row["ticker"] for row in _select_rows(user_id)]


def get_startup_tickers(user_id: str = DEFAULT_USER_ID) -> list[str]:
    """Watched tickers plus any still held, used by the lifespan to seed the feed.

    :func:`remove_ticker` keeps a held-but-unwatched ticker on the live feed, but
    that only holds for the running process. Seeding from the watchlist alone
    would drop such a position after a restart, freezing its P&L at cost and
    making it unsellable — ``execute_trade`` rejects any order without a cached
    price. Watchlist order is preserved; held extras are appended.
    """
    tickers = get_watchlist_tickers(user_id)
    watched = set(tickers)
    tickers.extend(t for t in get_held_tickers(user_id) if t not in watched)
    return tickers


# --- Public API ---


async def get_watchlist(user_id: str = DEFAULT_USER_ID) -> WatchlistResponse:
    """Watched tickers with their latest cached prices, oldest first."""
    rows = await to_thread.run_sync(_select_rows, user_id)
    return WatchlistResponse(
        tickers=[_build_item(row["ticker"], row["added_at"]) for row in rows]
    )


async def add_ticker(ticker: str, user_id: str = DEFAULT_USER_ID) -> WatchlistItem:
    """Add a ticker to the watchlist and start streaming prices for it.

    Raises:
        HTTPException: 400 for an invalid symbol, 409 if it is already watched.
    """
    ticker = _validate_ticker(ticker)
    added_at = await to_thread.run_sync(_insert_row, ticker, user_id)

    try:
        await get_market_source().add_ticker(ticker)
    except RuntimeError:
        # No source running (unit tests, or a request racing startup) — the next
        # start() call picks the ticker up from the database anyway.
        logger.debug("Market source not running; %s not added to the live feed", ticker)

    return _build_item(ticker, added_at)


async def remove_ticker(ticker: str, user_id: str = DEFAULT_USER_ID) -> None:
    """Remove a ticker from the watchlist and stop streaming prices for it.

    A ticker the user still holds keeps its price feed: dropping it would freeze
    the position's P&L at cost and make it unsellable, because
    :func:`app.services.portfolio.execute_trade` rejects any order without a
    cached price. The feed is released later, when the sell that closes the
    position finds the ticker off the watchlist.

    Raises:
        HTTPException: 404 if the ticker is not in the watchlist.
    """
    ticker = normalize_ticker(ticker)
    await to_thread.run_sync(_delete_row, ticker, user_id)

    if await to_thread.run_sync(has_position, ticker, user_id):
        logger.info("%s is still held; keeping it on the live feed after removal", ticker)
        return

    try:
        await get_market_source().remove_ticker(ticker)
    except RuntimeError:
        logger.debug("Market source not running; %s not removed from the live feed", ticker)
