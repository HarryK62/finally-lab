"""Pytest configuration and fixtures."""

import pytest


@pytest.fixture(autouse=True)
def no_developer_credentials(monkeypatch):
    """Scrub provider credentials so no test depends on the developer's `.env`.

    `app.config` loads the repo-root `.env` at import time. Without this, a
    developer who has configured `MASSIVE_API_KEY` gets `MassiveDataSource` in
    every lifespan test — which then makes real HTTP calls to api.massive.com and
    fails on prices that never arrive. Tests must be hermetic and offline.
    """
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


@pytest.fixture
def event_loop_policy():
    """Use the default event loop policy for all async tests."""
    import asyncio

    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point DB_PATH at a fresh temporary database and initialize it.

    Settings are read from the environment on every call, so monkeypatching
    DB_PATH is all that is needed to isolate a test's database.
    """
    from app.db import database

    db_path = tmp_path / "finally.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    database.reset_state()
    database.init_db()
    yield db_path
    database.reset_state()


@pytest.fixture
def price_cache():
    """A clean process-wide PriceCache, restored after the test."""
    from app import runtime

    runtime.reset_runtime()
    cache = runtime.get_price_cache()
    yield cache
    runtime.reset_runtime()


@pytest.fixture
def seeded_prices(price_cache):
    """Deterministic prices for the tickers used across the service/API tests."""
    for ticker, price in (("AAPL", 190.00), ("GOOGL", 175.00), ("MSFT", 420.00)):
        price_cache.update(ticker, price)
    return price_cache


@pytest.fixture
def client(temp_db, seeded_prices, monkeypatch, tmp_path):
    """A TestClient over a freshly built app, with no lifespan and no static dir.

    The app is constructed *after* ``price_cache`` has reset the runtime so the
    SSE router binds the same cache the tests seed. Deliberately not used as a
    context manager: that would run the lifespan, which starts a real market data
    simulator and a background snapshot task neither of which these tests want.
    """
    from fastapi.testclient import TestClient

    from app.main import create_app

    monkeypatch.setenv("STATIC_DIR", str(tmp_path / "no-such-static-dir"))
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MOCK", raising=False)
    return TestClient(create_app())
