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


def test_fundamental_score_new_metrics_positive(analyzer):
    """New metrics (PEG, P/B, P/S, EV/EBITDA, FCF yield, margins, beta) should boost score."""
    score = analyzer._calculate_fundamental_score(
        pe_ratio=18,
        forward_pe=14,
        eps=3.0,
        dividend_yield=0.03,
        debt_to_equity=0.4,
        current_ratio=2.0,
        quick_ratio=1.5,
        roa=0.12,
        roe=0.22,
        revenue_growth=0.18,
        profit_margin=0.18,
        operating_margin=0.22,
        peg_ratio=0.9,
        pb_ratio=1.2,
        price_to_sales=1.8,
        ev_ebitda=8.0,
        fcf_yield=0.06,
        beta=1.0,
    )
    assert score >= 70, f"Strongly positive metrics should yield score >= 70, got {score}"


def test_fundamental_score_new_metrics_negative(analyzer):
    """Overvalued, loss-making, high-debt companies should have low scores."""
    score = analyzer._calculate_fundamental_score(
        pe_ratio=60,
        forward_pe=55,
        eps=-2.0,
        dividend_yield=0.0,
        debt_to_equity=3.0,
        current_ratio=0.7,
        quick_ratio=0.4,
        roa=-0.03,
        roe=-0.10,
        revenue_growth=-0.15,
        profit_margin=-0.12,
        operating_margin=-0.08,
        peg_ratio=4.0,
        pb_ratio=8.0,
        price_to_sales=15.0,
        ev_ebitda=30.0,
        fcf_yield=-0.03,
        beta=3.0,
    )
    assert score <= 30, f"Negative/overvalued metrics should yield score <= 30, got {score}"


def test_fundamental_score_peg_below_one(analyzer):
    """PEG ratio < 1.0 should add +7 to score."""
    base = analyzer._calculate_fundamental_score(eps=2.0)
    with_good_peg = analyzer._calculate_fundamental_score(eps=2.0, peg_ratio=0.8)
    assert with_good_peg > base, "Good PEG ratio should increase score"


def test_fundamental_score_fcf_yield_positive(analyzer):
    """High FCF yield should boost score."""
    base = analyzer._calculate_fundamental_score(eps=2.0)
    with_fcf = analyzer._calculate_fundamental_score(eps=2.0, fcf_yield=0.06)
    assert with_fcf > base, "Positive FCF yield should increase score"


def test_fundamental_score_high_beta_penalty(analyzer):
    """Beta > 2.5 should reduce score."""
    base = analyzer._calculate_fundamental_score(eps=2.0)
    with_high_beta = analyzer._calculate_fundamental_score(eps=2.0, beta=3.0)
    assert with_high_beta < base, "High beta should reduce score"


def test_fundamental_analyze_returns_new_fields(analyzer, monkeypatch):
    """analyze() should populate the new fields when data is available."""
    stock = MagicMock()
    stock.info = {
        "trailingPE": 18.0,
        "forwardPE": 15.0,
        "trailingEps": 3.0,
        "forwardEps": 3.5,
        "dividendYield": 0.02,
        "debtToEquity": 0.4,
        "currentRatio": 2.0,
        "quickRatio": 1.5,
        "returnOnAssets": 0.10,
        "returnOnEquity": 0.20,
        "profitMargins": 0.15,
        "operatingMargins": 0.18,
        "pegRatio": 1.2,
        "priceToBook": 2.5,
        "priceToSalesTrailing12Months": 3.0,
        "enterpriseToEbitda": 12.0,
        "freeCashflow": 1_000_000_000,
        "marketCap": 20_000_000_000,
        "beta": 1.1,
    }
    stock.quarterly_financials = None

    monkeypatch.setattr(analyzer, "_fetch_quote_fallback", lambda s: {})
    monkeypatch.setattr(analyzer, "_fetch_web_fallback", lambda s: {})

    result = analyzer.analyze("AAPL", stock=stock, info=stock.info)

    assert result is not None
    assert result.forward_pe == 15.0
    assert result.quick_ratio == 1.5
    assert result.operating_margin == 0.18
    assert result.pb_ratio == 2.5
    assert result.price_to_sales == 3.0
    assert result.ev_ebitda == 12.0
    assert result.beta == 1.1
    assert result.fcf_yield == pytest.approx(0.05, rel=1e-3)
    assert result.eps_growth is not None  # forward_eps > trailing_eps
