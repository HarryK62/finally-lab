"""HTTP-level tests for the portfolio endpoints.

These use a bare app assembled from the routers — no lifespan, so the simulator is
not running and prices stay deterministic. Full-app wiring is covered by
``test_api_main.py``.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import portfolio as portfolio_router


@pytest.fixture
def client(temp_db, seeded_prices):
    app = FastAPI()
    app.include_router(portfolio_router.router)
    with TestClient(app) as test_client:
        yield test_client


def test_get_portfolio(client):
    response = client.get("/api/portfolio")
    assert response.status_code == 200
    body = response.json()
    assert body["cash_balance"] == 10000.0
    assert body["positions"] == []
    assert body["total_value"] == 10000.0
    assert set(body) == {
        "cash_balance",
        "positions",
        "positions_value",
        "total_value",
        "total_cost_basis",
        "total_unrealized_pnl",
        "total_unrealized_pnl_percent",
    }


def test_buy_then_read_back(client):
    response = client.post(
        "/api/portfolio/trade", json={"ticker": "aapl", "quantity": 10, "side": "buy"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["trade"]["ticker"] == "AAPL"
    assert body["trade"]["total"] == 1900.0
    assert body["trade"]["executed_at"].endswith("Z")
    assert body["cash_balance"] == 8100.0
    assert body["position"]["quantity"] == 10.0

    portfolio = client.get("/api/portfolio").json()
    assert portfolio["positions"][0]["ticker"] == "AAPL"
    assert portfolio["cash_balance"] == 8100.0


def test_sell_closes_the_position(client):
    client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 5, "side": "buy"})
    response = client.post(
        "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 5, "side": "sell"}
    )
    assert response.status_code == 200
    assert response.json()["position"] is None
    assert response.json()["cash_balance"] == 10000.0


def test_oversell_is_400_with_detail(client):
    client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "buy"})
    response = client.post(
        "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 5, "side": "sell"}
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Insufficient shares: trying to sell 5, hold 1"}


def test_insufficient_cash_is_400(client):
    response = client.post(
        "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 999, "side": "buy"}
    )
    assert response.status_code == 400
    assert response.json()["detail"].startswith("Insufficient cash: need $")


def test_unknown_ticker_is_400(client):
    response = client.post(
        "/api/portfolio/trade", json={"ticker": "ZZZZ", "quantity": 1, "side": "buy"}
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "No price available for ZZZZ"}


def test_malformed_trade_body_is_422(client):
    assert client.post("/api/portfolio/trade", json={"ticker": "AAPL"}).status_code == 422


def test_nan_quantity_is_400_not_500(client):
    """Python's json accepts the non-standard NaN literal; the guard must too."""
    response = client.post(
        "/api/portfolio/trade",
        content=b'{"ticker": "AAPL", "quantity": NaN, "side": "buy"}',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Quantity must be greater than zero"}


def test_history_is_empty_then_populated(client):
    assert client.get("/api/portfolio/history").json() == {"snapshots": []}

    client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "buy"})
    snapshots = client.get("/api/portfolio/history").json()["snapshots"]
    assert len(snapshots) == 1
    assert set(snapshots[0]) == {"total_value", "recorded_at"}


def test_history_limit_is_validated(client):
    assert client.get("/api/portfolio/history?limit=0").status_code == 422
    assert client.get("/api/portfolio/history?limit=99999").status_code == 422
    assert client.get("/api/portfolio/history?limit=10").status_code == 200
