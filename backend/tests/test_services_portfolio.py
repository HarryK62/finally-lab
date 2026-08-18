"""Tests for the portfolio service: valuation, trade math and value history.

Trade rules under test are pinned by ``planning/CONTRACTS.md`` §5 — including the
exact error message wording, which the chat flow surfaces verbatim to the user.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.db import database
from app.services import portfolio as svc

STARTING_CASH = 10000.0


def _held(ticker: str = "AAPL") -> tuple[float, float] | None:
    """The (quantity, avg_cost) currently stored for a ticker, or None."""
    with database.get_connection() as conn:
        row = conn.execute(
            "SELECT quantity, avg_cost FROM positions WHERE user_id = 'default' AND ticker = ?",
            (ticker,),
        ).fetchone()
    return (row["quantity"], row["avg_cost"]) if row else None


# --- Empty portfolio ---


def test_empty_portfolio_has_starting_cash_and_no_divide_by_zero(temp_db, seeded_prices):
    portfolio = svc.get_portfolio()
    assert portfolio.cash_balance == pytest.approx(STARTING_CASH)
    assert portfolio.positions == []
    assert portfolio.positions_value == 0.0
    assert portfolio.total_value == pytest.approx(STARTING_CASH)
    assert portfolio.total_cost_basis == 0.0
    assert portfolio.total_unrealized_pnl == 0.0
    # The contract requires 0.0 rather than a ZeroDivisionError here.
    assert portfolio.total_unrealized_pnl_percent == 0.0


# --- Buying ---


def test_buy_deducts_cash_and_creates_the_position(temp_db, seeded_prices):
    result = svc.execute_trade("AAPL", 10, "buy")

    assert result.trade.side == "buy"
    assert result.trade.price == 190.00
    assert result.trade.total == 1900.00
    assert result.trade.quantity == 10.0
    assert result.cash_balance == pytest.approx(8100.00)
    assert result.position is not None
    assert result.position.quantity == 10.0
    assert result.position.avg_cost == 190.00
    assert _held() == (10.0, 190.0)


def test_buy_normalizes_the_ticker(temp_db, seeded_prices):
    result = svc.execute_trade("  aapl  ", 1, "buy")
    assert result.trade.ticker == "AAPL"
    assert _held() is not None


def test_buy_accepts_uppercase_side(temp_db, seeded_prices):
    assert svc.execute_trade("AAPL", 1, "BUY").trade.side == "buy"


def test_repeated_buys_average_the_cost(temp_db, seeded_prices):
    svc.execute_trade("AAPL", 10, "buy")  # 10 @ 190
    seeded_prices.update("AAPL", 210.00)
    result = svc.execute_trade("AAPL", 10, "buy")  # 10 @ 210

    # (10*190 + 10*210) / 20 == 200
    assert result.position.quantity == 20.0
    assert result.position.avg_cost == 200.00
    quantity, avg_cost = _held()
    assert quantity == pytest.approx(20.0)
    assert avg_cost == pytest.approx(200.0)


def test_repeated_buys_average_cost_is_quantity_weighted(temp_db, seeded_prices):
    svc.execute_trade("AAPL", 30, "buy")  # 30 @ 190
    seeded_prices.update("AAPL", 290.00)
    svc.execute_trade("AAPL", 10, "buy")  # 10 @ 290

    # (30*190 + 10*290) / 40 == 215, not the unweighted mean of 240.
    _, avg_cost = _held()
    assert avg_cost == pytest.approx(215.0)


def test_buy_supports_fractional_shares(temp_db, seeded_prices):
    result = svc.execute_trade("AAPL", 0.5, "buy")
    assert result.trade.quantity == 0.5
    assert result.trade.total == 95.00
    assert result.cash_balance == pytest.approx(9905.00)


def test_buy_with_insufficient_cash_is_rejected_with_the_exact_message(temp_db, seeded_prices):
    with pytest.raises(HTTPException) as exc_info:
        svc.execute_trade("AAPL", 100, "buy")  # 19000 > 10000

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Insufficient cash: need $19000.00, have $10000.00"
    assert _held() is None


def test_a_rejected_buy_leaves_cash_untouched(temp_db, seeded_prices):
    with pytest.raises(HTTPException):
        svc.execute_trade("AAPL", 100, "buy")
    assert svc.get_portfolio().cash_balance == pytest.approx(STARTING_CASH)


def test_buy_spending_the_entire_balance_is_allowed(temp_db, price_cache):
    price_cache.update("AAPL", 100.0)
    result = svc.execute_trade("AAPL", 100, "buy")  # exactly $10,000
    assert result.cash_balance == pytest.approx(0.0)


# --- Selling ---


def test_sell_credits_cash_and_reduces_the_position(temp_db, seeded_prices):
    svc.execute_trade("AAPL", 10, "buy")
    result = svc.execute_trade("AAPL", 4, "sell")

    assert result.trade.side == "sell"
    assert result.trade.total == 760.00
    assert result.cash_balance == pytest.approx(8860.00)
    assert result.position.quantity == 6.0


def test_sell_does_not_change_average_cost(temp_db, seeded_prices):
    svc.execute_trade("AAPL", 10, "buy")  # avg cost 190
    seeded_prices.update("AAPL", 250.00)
    result = svc.execute_trade("AAPL", 5, "sell")  # sold higher

    assert result.position.avg_cost == 190.00
    assert _held()[1] == pytest.approx(190.0)


def test_selling_at_a_loss_does_not_change_average_cost(temp_db, seeded_prices):
    svc.execute_trade("AAPL", 10, "buy")
    seeded_prices.update("AAPL", 100.00)
    result = svc.execute_trade("AAPL", 5, "sell")

    assert result.position.avg_cost == 190.00
    assert result.position.unrealized_pnl < 0


def test_selling_the_whole_position_deletes_the_row(temp_db, seeded_prices):
    svc.execute_trade("AAPL", 10, "buy")
    result = svc.execute_trade("AAPL", 10, "sell")

    assert result.position is None
    assert _held() is None
    assert result.cash_balance == pytest.approx(STARTING_CASH)


def test_a_float_dust_residue_closes_the_position_out(temp_db, seeded_prices):
    """0.1 * 3 != 0.3 in binary floating point; the 1e-9 tolerance absorbs it."""
    svc.execute_trade("AAPL", 0.1, "buy")
    svc.execute_trade("AAPL", 0.1, "buy")
    svc.execute_trade("AAPL", 0.1, "buy")
    result = svc.execute_trade("AAPL", 0.3, "sell")

    assert result.position is None
    assert _held() is None


def test_sell_more_than_held_is_rejected_with_the_exact_message(temp_db, seeded_prices):
    svc.execute_trade("AAPL", 5, "buy")

    with pytest.raises(HTTPException) as exc_info:
        svc.execute_trade("AAPL", 8, "sell")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Insufficient shares: trying to sell 8, hold 5"
    assert _held()[0] == pytest.approx(5.0)


def test_sell_with_no_position_at_all_is_rejected(temp_db, seeded_prices):
    with pytest.raises(HTTPException) as exc_info:
        svc.execute_trade("AAPL", 1, "sell")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Insufficient shares: trying to sell 1, hold 0"


def test_selling_a_hair_more_than_held_is_within_tolerance(temp_db, seeded_prices):
    svc.execute_trade("AAPL", 5, "buy")
    # Inside the 1e-9 tolerance, so it closes the position rather than erroring.
    result = svc.execute_trade("AAPL", 5 + 1e-12, "sell")
    assert result.position is None


# --- Validation ---


@pytest.mark.parametrize("quantity", [0, -1, -0.5])
def test_non_positive_quantity_is_rejected(temp_db, seeded_prices, quantity):
    with pytest.raises(HTTPException) as exc_info:
        svc.execute_trade("AAPL", quantity, "buy")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Quantity must be greater than zero"


@pytest.mark.parametrize("quantity", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_quantity_is_rejected(temp_db, seeded_prices, quantity):
    """`NaN <= 0` is False, so NaN once reached SQLite and 500ed on NOT NULL."""
    with pytest.raises(HTTPException) as exc_info:
        svc.execute_trade("AAPL", quantity, "buy")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Quantity must be greater than zero"


def test_unknown_side_is_rejected(temp_db, seeded_prices):
    with pytest.raises(HTTPException) as exc_info:
        svc.execute_trade("AAPL", 1, "hodl")
    assert exc_info.value.status_code == 400


def test_unknown_ticker_has_no_price_and_is_rejected(temp_db, seeded_prices):
    with pytest.raises(HTTPException) as exc_info:
        svc.execute_trade("ZZZZ", 1, "buy")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "No price available for ZZZZ"


def test_a_trade_never_adds_the_ticker_to_the_watchlist(temp_db, price_cache):
    price_cache.update("PLTR", 50.0)
    svc.execute_trade("PLTR", 1, "buy")

    with database.get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM watchlist WHERE ticker = 'PLTR'"
        ).fetchone()
    assert row is None


# --- Valuation ---


def test_position_valuation_fields(temp_db, seeded_prices):
    svc.execute_trade("AAPL", 10, "buy")  # cost basis 1900
    seeded_prices.update("AAPL", 195.00)

    position = svc.get_portfolio().positions[0]
    assert position.current_price == 195.00
    assert position.market_value == 1950.00
    assert position.cost_basis == 1900.00
    assert position.unrealized_pnl == 50.00
    assert position.unrealized_pnl_percent == 2.63  # 50/1900 -> 2.6315...
    assert position.weight == 1.0  # only holding


def test_weights_sum_to_one_and_are_market_value_based(temp_db, seeded_prices):
    svc.execute_trade("AAPL", 10, "buy")  # 1900
    svc.execute_trade("MSFT", 10, "buy")  # 4200

    portfolio = svc.get_portfolio()
    weights = {p.ticker: p.weight for p in portfolio.positions}
    assert weights["MSFT"] == pytest.approx(4200 / 6100, abs=1e-4)
    assert weights["AAPL"] == pytest.approx(1900 / 6100, abs=1e-4)
    assert sum(weights.values()) == pytest.approx(1.0, abs=1e-3)


def test_positions_are_sorted_by_market_value_descending(temp_db, seeded_prices):
    svc.execute_trade("AAPL", 10, "buy")  # 1900
    svc.execute_trade("MSFT", 10, "buy")  # 4200
    svc.execute_trade("GOOGL", 10, "buy")  # 1750

    tickers = [p.ticker for p in svc.get_portfolio().positions]
    assert tickers == ["MSFT", "AAPL", "GOOGL"]


def test_totals_aggregate_across_positions(temp_db, seeded_prices):
    svc.execute_trade("AAPL", 10, "buy")  # 1900 spent
    svc.execute_trade("MSFT", 10, "buy")  # 4200 spent
    seeded_prices.update("AAPL", 200.00)  # +100
    seeded_prices.update("MSFT", 400.00)  # -200

    portfolio = svc.get_portfolio()
    assert portfolio.cash_balance == pytest.approx(3900.00)
    assert portfolio.positions_value == 6000.00
    assert portfolio.total_cost_basis == 6100.00
    assert portfolio.total_unrealized_pnl == -100.00
    assert portfolio.total_unrealized_pnl_percent == -1.64  # -100/6100
    assert portfolio.total_value == pytest.approx(9900.00)


def test_a_position_without_a_cached_price_falls_back_to_average_cost(temp_db, price_cache):
    price_cache.update("PLTR", 50.0)
    svc.execute_trade("PLTR", 10, "buy")
    price_cache.remove("PLTR")

    position = svc.get_portfolio().positions[0]
    assert position.current_price == 50.00
    assert position.market_value == 500.00
    assert position.unrealized_pnl == 0.0
    assert position.unrealized_pnl_percent == 0.0


# --- History and snapshots ---


def test_history_is_empty_before_anything_happens(temp_db, seeded_prices):
    assert svc.get_history().snapshots == []


def test_every_trade_records_a_snapshot(temp_db, seeded_prices):
    svc.execute_trade("AAPL", 1, "buy")
    svc.execute_trade("AAPL", 1, "sell")
    assert len(svc.get_history().snapshots) == 2


def test_record_snapshot_captures_the_current_total_value(temp_db, seeded_prices):
    svc.record_snapshot()
    snapshots = svc.get_history().snapshots
    assert len(snapshots) == 1
    assert snapshots[0].total_value == pytest.approx(STARTING_CASH)
    assert snapshots[0].recorded_at.endswith("Z")


def test_history_is_ascending_and_limit_keeps_the_most_recent(temp_db, seeded_prices):
    for _ in range(5):
        svc.record_snapshot()

    all_snapshots = svc.get_history().snapshots
    assert len(all_snapshots) == 5
    stamps = [s.recorded_at for s in all_snapshots]
    assert stamps == sorted(stamps)

    limited = svc.get_history(limit=2).snapshots
    assert len(limited) == 2
    # The most recent two, still oldest-first.
    assert [s.recorded_at for s in limited] == stamps[-2:]


def test_history_limit_is_capped(temp_db, seeded_prices):
    svc.record_snapshot()
    assert len(svc.get_history(limit=10**9).snapshots) == 1


# --- Trade log ---


def test_trades_are_appended_to_the_trade_log(temp_db, seeded_prices):
    svc.execute_trade("AAPL", 2, "buy")
    svc.execute_trade("AAPL", 1, "sell")

    with database.get_connection() as conn:
        rows = conn.execute(
            "SELECT ticker, side, quantity, price FROM trades ORDER BY rowid"
        ).fetchall()

    assert [(r["ticker"], r["side"], r["quantity"]) for r in rows] == [
        ("AAPL", "buy", 2.0),
        ("AAPL", "sell", 1.0),
    ]


def test_a_rejected_trade_writes_no_trade_row(temp_db, seeded_prices):
    with pytest.raises(HTTPException):
        svc.execute_trade("AAPL", 1000, "buy")

    with database.get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()["n"]
    assert count == 0


def test_normalize_ticker():
    assert svc.normalize_ticker("  aapl ") == "AAPL"
