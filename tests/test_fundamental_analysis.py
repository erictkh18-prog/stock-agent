"""Test suite for fundamental analysis"""
from unittest.mock import MagicMock

import pytest
import requests
from src.fundamental_analysis import FundamentalAnalyzer

@pytest.fixture
def analyzer():
    return FundamentalAnalyzer()

def test_fundamental_score_calculation(analyzer):
    """Test fundamental score calculation logic"""
    # Test with neutral values
    score = analyzer._calculate_fundamental_score(
        pe_ratio=20,
        eps=1.5,
        dividend_yield=0.02,
        debt_to_equity=0.8,
        current_ratio=2.0,
        roa=0.08,
        roe=0.15,
        revenue_growth=0.10
    )
    assert 50 <= score <= 100, "Score should be in reasonable range"

def test_fundamental_score_with_none_values(analyzer):
    """Test score calculation with some None values"""
    score = analyzer._calculate_fundamental_score(
        pe_ratio=None,
        eps=1.5,
        dividend_yield=None,
        debt_to_equity=0.5,
        current_ratio=None,
        roa=0.08,
        roe=None,
        revenue_growth=0.05
    )
    assert 0 <= score <= 100, "Score should handle None values"


def test_quote_fallback_enters_backoff_on_401(analyzer, monkeypatch):
    """A 401 response should activate quote backoff and avoid repeated requests."""
    calls = {"count": 0}

    class FakeResponse:
        status_code = 401

        def raise_for_status(self):
            raise requests.HTTPError("401 unauthorized", response=self)

    def fake_get(*args, **kwargs):
        calls["count"] += 1
        return FakeResponse()

    monkeypatch.setattr("requests.get", fake_get)

    first = analyzer._fetch_quote_fallback("AAPL")
    second = analyzer._fetch_quote_fallback("MSFT")

    assert first == {}
    assert second == {}
    assert calls["count"] == 1
    assert analyzer.quote_backoff_until is not None


def test_analyze_skips_quote_fallback_when_only_peg_ratio_is_missing(analyzer, monkeypatch):
    """Missing peg ratio alone should not trigger the quote fallback."""
    stock = MagicMock()
    stock.info = {
        "trailingPE": 20.0,
        "trailingEps": 5.0,
        "dividendYield": 0.02,
        "profitMargins": 0.15,
    }
    stock.quarterly_financials = None

    quote_calls = {"count": 0}

    def fake_quote(symbol):
        quote_calls["count"] += 1
        return {}

    monkeypatch.setattr(analyzer, "_fetch_quote_fallback", fake_quote)
    monkeypatch.setattr(analyzer, "_fetch_web_fallback", lambda symbol: {})

    result = analyzer.analyze("AAPL", stock=stock, info=stock.info)

    assert result is not None
    assert quote_calls["count"] == 0
