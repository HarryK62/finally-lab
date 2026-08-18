"""Tests for environment parsing in `app.config`.

Settings are read fresh on every `get_settings()` call, so these only need to
set an environment variable and read it back.
"""

from __future__ import annotations

import pytest

from app.config import DEFAULT_SNAPSHOT_RETENTION_DAYS, get_settings


class TestSnapshotRetention:
    def test_defaults_when_unset(self, monkeypatch):
        monkeypatch.delenv("SNAPSHOT_RETENTION_DAYS", raising=False)
        assert get_settings().SNAPSHOT_RETENTION_DAYS == DEFAULT_SNAPSHOT_RETENTION_DAYS

    @pytest.mark.parametrize(("raw", "expected"), [("2", 2), (" 30 ", 30), ("0", 0), ("-1", -1)])
    def test_parses_an_integer(self, monkeypatch, raw, expected):
        monkeypatch.setenv("SNAPSHOT_RETENTION_DAYS", raw)
        assert get_settings().SNAPSHOT_RETENTION_DAYS == expected

    @pytest.mark.parametrize("raw", ["", "   ", "seven", "7.5", "7d"])
    def test_falls_back_on_anything_unparseable(self, monkeypatch, raw):
        """A typo must not stop the app booting, and must not delete more."""
        monkeypatch.setenv("SNAPSHOT_RETENTION_DAYS", raw)
        assert get_settings().SNAPSHOT_RETENTION_DAYS == DEFAULT_SNAPSHOT_RETENTION_DAYS


class TestLlmMock:
    @pytest.mark.parametrize("raw", ["true", "TRUE", " True ", "1"])
    def test_truthy_values(self, monkeypatch, raw):
        monkeypatch.setenv("LLM_MOCK", raw)
        assert get_settings().LLM_MOCK is True

    @pytest.mark.parametrize("raw", ["false", "no", "0", ""])
    def test_everything_else_is_false(self, monkeypatch, raw):
        monkeypatch.setenv("LLM_MOCK", raw)
        assert get_settings().LLM_MOCK is False

    def test_defaults_to_false_when_unset(self, monkeypatch):
        monkeypatch.delenv("LLM_MOCK", raising=False)
        assert get_settings().LLM_MOCK is False


class TestMarketSourceName:
    def test_massive_when_a_key_is_present(self, monkeypatch):
        monkeypatch.setenv("MASSIVE_API_KEY", "some-key")
        assert get_settings().market_source_name == "massive"

    @pytest.mark.parametrize("raw", ["", "   "])
    def test_simulator_when_the_key_is_blank(self, monkeypatch, raw):
        """A whitespace-only value reads as unset — see the E2E config."""
        monkeypatch.setenv("MASSIVE_API_KEY", raw)
        assert get_settings().market_source_name == "simulator"
