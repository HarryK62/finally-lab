"""Tests for watchlist CRUD and market data source synchronization."""

from __future__ import annotations

import pytest
from anyio import to_thread
from fastapi import HTTPException

from app import runtime
from app.market import MarketDataSource
from app.market.seed_prices import SEED_PRICES
from app.services import portfolio as portfolio_svc
from app.services import watchlist as svc


class FakeSource(MarketDataSource):
    """Records add/remove calls so tests can assert the feed stays in sync."""

    def __init__(self) -> None:
        self.tickers: list[str] = []
        self.added: list[str] = []
        self.removed: list[str] = []

    async def start(self, tickers: list[str]) -> None:
        self.tickers = list(tickers)

    async def stop(self) -> None:
        self.tickers = []

    async def add_ticker(self, ticker: str) -> None:
        self.added.append(ticker)
        self.tickers.append(ticker)

    async def remove_ticker(self, ticker: str) -> None:
        self.removed.append(ticker)
        if ticker in self.tickers:
            self.tickers.remove(ticker)

    def get_tickers(self) -> list[str]:
        return list(self.tickers)


class CacheEvictingSource(FakeSource):
    """A fake that behaves like the running simulator around the price cache.

    Two details matter to the position-lifecycle tests below: ``remove_ticker``
    evicts the cached price (as ``SimulatorDataSource`` does), and prices only
    arrive for tickers the source is still tracking. Together they reproduce the
    bug those tests pin — a dropped feed leaves a holding unpriceable, and so
    unsellable.
    """

    async def remove_ticker(self, ticker: str) -> None:
        await super().remove_ticker(ticker)
        runtime.get_price_cache().remove(ticker)

    def tick(self, ticker: str, price: float) -> None:
        """Publish a price, but only for a ticker the feed still carries."""
        if ticker in self.tickers:
            runtime.get_price_cache().update(ticker, price)


@pytest.fixture
def fake_source(price_cache):
    source = FakeSource()
    runtime.set_market_source(source)
    yield source
    runtime.set_market_source(None)


@pytest.fixture
def evicting_source(price_cache):
    """A running feed carrying the default watchlist, as the lifespan starts it."""
    source = CacheEvictingSource()
    source.tickers = list(SEED_PRICES)
    runtime.set_market_source(source)
    yield source
    runtime.set_market_source(None)


async def test_default_watchlist_is_returned_in_insertion_order(temp_db, price_cache):
    result = await svc.get_watchlist()
    assert [item.ticker for item in result.tickers] == list(SEED_PRICES)


async def test_items_without_a_cached_price_are_flat(temp_db, price_cache):
    item = (await svc.get_watchlist()).tickers[0]
    assert item.price is None
    assert item.previous_price is None
    assert item.change == 0.0
    assert item.change_percent == 0.0
    assert item.direction == "flat"


async def test_items_carry_the_cached_price_and_direction(temp_db, price_cache):
    price_cache.update("AAPL", 190.00)
    price_cache.update("AAPL", 195.00)

    item = next(i for i in (await svc.get_watchlist()).tickers if i.ticker == "AAPL")
    assert item.price == pytest.approx(195.0)
    assert item.previous_price == pytest.approx(190.0)
    assert item.change == pytest.approx(5.0)
    assert item.change_percent == pytest.approx(2.63)
    assert item.direction == "up"


async def test_add_ticker_normalizes_and_syncs_the_feed(temp_db, fake_source):
    item = await svc.add_ticker("  pypl ")

    assert item.ticker == "PYPL"
    assert fake_source.added == ["PYPL"]
    assert "PYPL" in [i.ticker for i in (await svc.get_watchlist()).tickers]


async def test_add_duplicate_conflicts(temp_db, fake_source):
    with pytest.raises(HTTPException) as exc:
        await svc.add_ticker("aapl")
    assert exc.value.status_code == 409
    assert exc.value.detail == "AAPL is already in the watchlist"
    assert fake_source.added == []


@pytest.mark.parametrize("ticker", ["", "   ", "TOOLONGTICKER", "AA PL", "AA1", "$$$"])
async def test_invalid_symbols_are_rejected(temp_db, fake_source, ticker):
    with pytest.raises(HTTPException) as exc:
        await svc.add_ticker(ticker)
    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid ticker symbol"


async def test_remove_ticker_syncs_the_feed(temp_db, fake_source):
    await svc.remove_ticker("aapl")

    assert fake_source.removed == ["AAPL"]
    assert "AAPL" not in [i.ticker for i in (await svc.get_watchlist()).tickers]


async def test_remove_missing_ticker_is_404(temp_db, fake_source):
    with pytest.raises(HTTPException) as exc:
        await svc.remove_ticker("pypl")
    assert exc.value.status_code == 404
    assert exc.value.detail == "PYPL is not in the watchlist"


async def test_mutations_work_without_a_running_market_source(temp_db, price_cache):
    """Unit tests and requests racing startup must not blow up on a missing source."""
    runtime.set_market_source(None)
    item = await svc.add_ticker("PYPL")
    assert item.ticker == "PYPL"
    await svc.remove_ticker("PYPL")


def test_get_watchlist_tickers_helper(temp_db):
    assert svc.get_watchlist_tickers() == list(SEED_PRICES)


# --- Feed lifecycle for held tickers ---
#
# Removing a ticker from the watchlist used to stop its price feed unconditionally,
# which froze the P&L of any open position at cost and made it permanently
# unsellable ("No price available for TSLA"). A held ticker now keeps streaming
# until the sell that closes it out.


async def _trade(ticker: str, quantity: float, side: str):
    """Execute a trade the way the API does — on an anyio worker thread.

    The post-sell feed cleanup hands an async call back to the event loop from
    that thread, so trades run here rather than being called synchronously.
    """
    return await to_thread.run_sync(portfolio_svc.execute_trade, ticker, quantity, side)


async def test_removing_a_held_ticker_keeps_it_on_the_feed(temp_db, price_cache, evicting_source):
    evicting_source.tick("TSLA", 250.03)
    await _trade("TSLA", 5, "buy")

    await svc.remove_ticker("TSLA")

    assert evicting_source.removed == []
    assert price_cache.get_price("TSLA") == pytest.approx(250.03)
    assert "TSLA" not in [i.ticker for i in (await svc.get_watchlist()).tickers]


async def test_a_removed_but_held_position_still_prices_live(temp_db, price_cache, evicting_source):
    evicting_source.tick("TSLA", 250.00)
    await _trade("TSLA", 5, "buy")
    await svc.remove_ticker("TSLA")

    evicting_source.tick("TSLA", 260.00)  # A feed tick after the removal

    position = portfolio_svc.get_portfolio().positions[0]
    assert position.ticker == "TSLA"
    assert position.current_price == 260.00
    assert position.current_price != position.avg_cost
    assert position.unrealized_pnl == 50.00


async def test_a_removed_but_held_position_can_still_be_sold(temp_db, price_cache, evicting_source):
    evicting_source.tick("TSLA", 250.00)
    await _trade("TSLA", 5, "buy")
    await svc.remove_ticker("TSLA")
    evicting_source.tick("TSLA", 260.00)

    result = await _trade("TSLA", 5, "sell")

    assert result.trade.price == 260.00
    assert result.trade.total == 1300.00
    assert result.position is None
    assert result.cash_balance == pytest.approx(10050.00)


async def test_closing_out_an_unwatched_position_releases_the_feed(
    temp_db, price_cache, evicting_source
):
    evicting_source.tick("TSLA", 250.00)
    await _trade("TSLA", 5, "buy")
    await svc.remove_ticker("TSLA")

    await _trade("TSLA", 5, "sell")

    assert evicting_source.removed == ["TSLA"]
    assert price_cache.get_price("TSLA") is None


async def test_a_partial_sell_keeps_the_feed(temp_db, price_cache, evicting_source):
    evicting_source.tick("TSLA", 250.00)
    await _trade("TSLA", 5, "buy")
    await svc.remove_ticker("TSLA")

    await _trade("TSLA", 2, "sell")

    assert evicting_source.removed == []
    assert price_cache.get_price("TSLA") == pytest.approx(250.00)


async def test_closing_out_a_watched_position_keeps_the_feed(temp_db, price_cache, evicting_source):
    """AAPL is still on the watchlist, so the user is still watching its price."""
    evicting_source.tick("AAPL", 190.00)
    await _trade("AAPL", 5, "buy")

    await _trade("AAPL", 5, "sell")

    assert evicting_source.removed == []
    assert price_cache.get_price("AAPL") == pytest.approx(190.00)


async def test_removing_an_unheld_ticker_still_stops_the_feed(
    temp_db, price_cache, evicting_source
):
    evicting_source.tick("AAPL", 190.00)

    await svc.remove_ticker("AAPL")

    assert evicting_source.removed == ["AAPL"]
    assert price_cache.get_price("AAPL") is None


def test_a_close_out_off_the_event_loop_leaves_the_feed_alone(
    temp_db, price_cache, evicting_source
):
    """Called synchronously there is no loop to run the async removal on.

    Both API and chat trades go through ``to_thread.run_sync``, so this only
    affects direct in-process calls; leaving the ticker streaming is the safe
    outcome either way.
    """
    price_cache.update("PLTR", 50.00)
    portfolio_svc.execute_trade("PLTR", 1, "buy")
    portfolio_svc.execute_trade("PLTR", 1, "sell")

    assert evicting_source.removed == []
    assert price_cache.get_price("PLTR") == pytest.approx(50.00)


# --- Startup seeding
#
# The feed protection above only lives in the running process. Seeding the source
# from the watchlist alone would drop a held-but-unwatched ticker at the next
# restart, which is exactly the state those tests exist to prevent.


async def test_get_startup_tickers_includes_a_held_but_unwatched_ticker(
    temp_db, price_cache, evicting_source
):
    evicting_source.tick("TSLA", 250.00)
    await _trade("TSLA", 5, "buy")
    await svc.remove_ticker("TSLA")

    tickers = svc.get_startup_tickers()

    watched = svc.get_watchlist_tickers()
    assert "TSLA" not in watched
    assert "TSLA" in tickers
    assert tickers == [*watched, "TSLA"]  # watchlist order first, held extras appended


async def test_get_startup_tickers_does_not_duplicate_a_watched_holding(
    temp_db, price_cache, evicting_source
):
    evicting_source.tick("AAPL", 190.00)
    await _trade("AAPL", 5, "buy")

    tickers = svc.get_startup_tickers()

    assert tickers.count("AAPL") == 1
    assert tickers == list(SEED_PRICES)


async def test_get_startup_tickers_drops_a_closed_out_position(
    temp_db, price_cache, evicting_source
):
    evicting_source.tick("TSLA", 250.00)
    await _trade("TSLA", 5, "buy")
    await svc.remove_ticker("TSLA")
    await _trade("TSLA", 5, "sell")

    assert "TSLA" not in svc.get_startup_tickers()


def test_get_held_tickers_ignores_dust(temp_db, price_cache):
    price_cache.update("PLTR", 50.00)
    portfolio_svc.execute_trade("PLTR", 1, "buy")
    portfolio_svc.execute_trade("PLTR", 1, "sell")

    assert portfolio_svc.get_held_tickers() == []
