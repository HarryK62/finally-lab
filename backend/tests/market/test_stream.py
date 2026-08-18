"""Tests for the SSE event generator.

`stream.py` was previously exercised only by the E2E suite, which cannot make a
producer stall on demand — the case the heartbeat exists for.
"""

from __future__ import annotations

import json

import pytest

from app.market.cache import PriceCache
from app.market.stream import _generate_events


class FakeRequest:
    """A request that reports disconnection after N checks.

    The generator loops until the client goes away, so every test needs a
    disconnect to terminate it.
    """

    client = None

    def __init__(self, disconnect_after: int) -> None:
        self._checks = 0
        self._disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self._checks += 1
        return self._checks > self._disconnect_after


async def _collect(cache: PriceCache, request: FakeRequest, **kwargs) -> list[str]:
    """Drain the generator to completion."""
    return [chunk async for chunk in _generate_events(cache, request, interval=0, **kwargs)]


@pytest.mark.asyncio
class TestHeartbeat:
    async def test_a_silent_stream_emits_a_keepalive(self):
        """A stalled producer must not leave the connection completely quiet."""
        cache = PriceCache()
        cache.update("AAPL", 190.0)

        chunks = await _collect(cache, FakeRequest(3), heartbeat_interval=0)

        # First pass sends data (version moved); later passes are silent, so the
        # keepalive is what keeps the connection warm.
        assert chunks[0] == "retry: 1000\n\n"
        assert any(c.startswith("data: ") for c in chunks)
        assert ": ping\n\n" in chunks

    async def test_a_keepalive_is_a_comment_not_an_event(self):
        """EventSource ignores comment lines, so the client contract is unchanged."""
        cache = PriceCache()

        chunks = await _collect(cache, FakeRequest(2), heartbeat_interval=0)

        pings = [c for c in chunks if c.startswith(":")]
        assert pings
        for ping in pings:
            assert ping.startswith(": ")
            assert not ping.startswith("data:")
            assert ping.endswith("\n\n")

    async def test_a_busy_stream_sends_no_keepalives(self):
        """Data events keep the connection warm on their own."""
        cache = PriceCache()

        chunks = []
        gen = _generate_events(cache, FakeRequest(3), interval=0, heartbeat_interval=0)
        price = 190.0
        async for chunk in gen:
            cache.update("AAPL", price)  # every pass moves the version
            price += 1.0
            chunks.append(chunk)

        assert ": ping\n\n" not in chunks

    async def test_the_default_interval_keeps_a_short_stream_quiet(self):
        """With the real 15s window, a brief stall produces no keepalive."""
        cache = PriceCache()

        chunks = await _collect(cache, FakeRequest(3))

        assert ": ping\n\n" not in chunks


@pytest.mark.asyncio
class TestEventShape:
    async def test_data_events_match_the_contract(self):
        """CONTRACTS.md §6: one object keyed by ticker, timestamp in Unix seconds."""
        cache = PriceCache()
        cache.update("AAPL", 195.0, timestamp=1755439402.48)

        chunks = await _collect(cache, FakeRequest(1))

        data_chunks = [c for c in chunks if c.startswith("data: ")]
        assert len(data_chunks) == 1
        payload = json.loads(data_chunks[0].removeprefix("data: ").strip())
        assert payload["AAPL"]["ticker"] == "AAPL"
        assert payload["AAPL"]["price"] == 195.0
        assert payload["AAPL"]["timestamp"] == 1755439402.48

    async def test_the_retry_directive_comes_first(self):
        cache = PriceCache()

        chunks = await _collect(cache, FakeRequest(1))

        assert chunks[0] == "retry: 1000\n\n"

    async def test_an_empty_cache_sends_no_data_event(self):
        cache = PriceCache()

        chunks = await _collect(cache, FakeRequest(2))

        assert not [c for c in chunks if c.startswith("data: ")]

    async def test_an_unchanged_version_is_not_resent(self):
        """Events are version-gated: the same prices are sent once, not per tick."""
        cache = PriceCache()
        cache.update("AAPL", 190.0)

        chunks = await _collect(cache, FakeRequest(4))

        assert len([c for c in chunks if c.startswith("data: ")]) == 1
