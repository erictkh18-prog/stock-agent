"""Tests for macro_regime module."""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.macro_regime import (
    MACRO_CACHE_TTL_SECONDS,
    _fetch_macro_data,
    _get_spy_trend,
    _get_vix,
    _get_yield_spread,
    get_macro_regime,
)


# ── _get_spy_trend ─────────────────────────────────────────────────────────────

def _make_hist(n: int, start: float = 100.0, step: float = 0.1):
    """Build a minimal Close-only DataFrame."""
    dates = pd.date_range("2024-01-01", periods=n)
    closes = [start + i * step for i in range(n)]
    return pd.DataFrame({"Close": closes, "High": closes, "Low": closes, "Volume": [1_000_000] * n}, index=dates)


def test_get_spy_trend_uptrend(monkeypatch):
    hist = _make_hist(260, start=80.0, step=0.5)  # steadily rising
    monkeypatch.setattr("src.macro_regime.yf.Ticker", lambda s: MagicMock(history=lambda **kw: hist))
    assert _get_spy_trend() == "uptrend"


def test_get_spy_trend_downtrend(monkeypatch):
    hist = _make_hist(260, start=200.0, step=-0.5)  # steadily falling
    monkeypatch.setattr("src.macro_regime.yf.Ticker", lambda s: MagicMock(history=lambda **kw: hist))
    assert _get_spy_trend() == "downtrend"


def test_get_spy_trend_returns_neutral_on_insufficient_data(monkeypatch):
    hist = _make_hist(40)  # fewer than 55 rows
    monkeypatch.setattr("src.macro_regime.yf.Ticker", lambda s: MagicMock(history=lambda **kw: hist))
    assert _get_spy_trend() == "neutral"


def test_get_spy_trend_returns_neutral_on_exception(monkeypatch):
    monkeypatch.setattr("src.macro_regime.yf.Ticker", lambda s: MagicMock(history=MagicMock(side_effect=RuntimeError("net"))))
    assert _get_spy_trend() == "neutral"


# ── _get_vix ───────────────────────────────────────────────────────────────────

def _make_vix_ticker(vix_close: float):
    hist = pd.DataFrame({"Close": [vix_close]}, index=pd.date_range("2024-01-01", periods=1))
    return MagicMock(history=lambda **kw: hist)


@pytest.mark.parametrize("vix,expected_signal", [
    (12.0, "low"),
    (17.0, "normal"),
    (25.0, "elevated"),
    (35.0, "high"),
    (45.0, "extreme"),
])
def test_get_vix_signal_buckets(monkeypatch, vix, expected_signal):
    monkeypatch.setattr("src.macro_regime.yf.Ticker", lambda s: _make_vix_ticker(vix))
    level, signal = _get_vix()
    assert signal == expected_signal
    assert level == round(vix, 2)


def test_get_vix_returns_unknown_on_empty(monkeypatch):
    monkeypatch.setattr("src.macro_regime.yf.Ticker", lambda s: MagicMock(history=lambda **kw: pd.DataFrame()))
    level, signal = _get_vix()
    assert signal == "unknown"
    assert level is None


# ── _get_yield_spread ──────────────────────────────────────────────────────────

def _make_yield_ticker(rate: float):
    hist = pd.DataFrame({"Close": [rate]}, index=pd.date_range("2024-01-01", periods=1))
    return MagicMock(history=lambda **kw: hist)


@pytest.mark.parametrize("rate10,rate3m,expected_signal", [
    (4.5, 3.5, "normal"),   # spread 1.0
    (4.1, 3.9, "flat"),     # spread 0.2
    (3.8, 5.2, "inverted"), # spread -1.4
])
def test_get_yield_spread_signals(monkeypatch, rate10, rate3m, expected_signal):
    calls = iter([_make_yield_ticker(rate10), _make_yield_ticker(rate3m)])
    monkeypatch.setattr("src.macro_regime.yf.Ticker", lambda s: next(calls))
    spread, signal = _get_yield_spread()
    assert signal == expected_signal
    assert spread == round(rate10 - rate3m, 3)


# ── get_macro_regime (integration + caching) ──────────────────────────────────

def test_get_macro_regime_returns_expected_keys(monkeypatch):
    monkeypatch.setattr("src.macro_regime._fetch_macro_data", lambda: {
        "regime": "bull",
        "spy_trend": "uptrend",
        "vix_level": 14.0,
        "vix_signal": "low",
        "yield_spread": 1.2,
        "yield_signal": "normal",
        "regime_score": 80.0,
    })
    # Clear cache before test
    import src.macro_regime as _m; _m._macro_cache.clear()
    result = get_macro_regime()
    assert result["regime"] == "bull"
    assert result["regime_score"] == 80.0


def test_get_macro_regime_caches_result(monkeypatch):
    """Second call should not re-invoke _fetch_macro_data."""
    call_count = {"n": 0}

    def fake_fetch():
        call_count["n"] += 1
        return {
            "regime": "neutral", "spy_trend": "neutral", "vix_level": 20.0,
            "vix_signal": "normal", "yield_spread": 0.3, "yield_signal": "flat",
            "regime_score": 55.0,
        }

    monkeypatch.setattr("src.macro_regime._fetch_macro_data", fake_fetch)
    import src.macro_regime as _m; _m._macro_cache.clear()
    get_macro_regime()
    get_macro_regime()
    assert call_count["n"] == 1, "Second call should be served from cache"


def test_fetch_macro_data_bear_regime(monkeypatch):
    """Downtrend + extreme VIX + inverted yield should produce bear regime."""
    monkeypatch.setattr("src.macro_regime._get_spy_trend", lambda: "downtrend")
    monkeypatch.setattr("src.macro_regime._get_vix", lambda: (45.0, "extreme"))
    monkeypatch.setattr("src.macro_regime._get_yield_spread", lambda: (-0.5, "inverted"))
    data = _fetch_macro_data()
    assert data["regime"] == "bear"
    assert data["regime_score"] < 40


def test_fetch_macro_data_bull_regime(monkeypatch):
    """Uptrend + low VIX + normal yield should produce bull regime."""
    monkeypatch.setattr("src.macro_regime._get_spy_trend", lambda: "uptrend")
    monkeypatch.setattr("src.macro_regime._get_vix", lambda: (12.0, "low"))
    monkeypatch.setattr("src.macro_regime._get_yield_spread", lambda: (1.5, "normal"))
    data = _fetch_macro_data()
    assert data["regime"] == "bull"
    assert data["regime_score"] >= 65
