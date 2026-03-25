"""Test suite for stock screener — covers improvements 2 and 3."""
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from src.models import (
    FundamentalAnalysis, TechnicalAnalysis, SentimentAnalysis,
    StockAnalysis, ScreeningFilter,
)
from src.stock_screener import StockScreener


def _make_analysis(symbol: str, overall_score: float = 75.0) -> StockAnalysis:
    """Helper to build a minimal StockAnalysis object."""
    return StockAnalysis(
        symbol=symbol,
        name=f"{symbol} Inc.",
        current_price=100.0,
        timestamp=datetime.now(timezone.utc),
        fundamental=FundamentalAnalysis(score=70.0),
        technical=TechnicalAnalysis(score=75.0),
        sentiment=SentimentAnalysis(score=80.0),
        overall_score=overall_score,
        recommendation="BUY",
        confidence=0.8,
    )


@pytest.fixture
def screener():
    return StockScreener()


# ---------------------------------------------------------------------------
# Improvement 2: _calculate_recommendation must accept SentimentAnalysis model
# ---------------------------------------------------------------------------

def test_calculate_recommendation_with_sentiment_model(screener):
    """_calculate_recommendation must accept a SentimentAnalysis model (not a dict)."""
    fundamental = FundamentalAnalysis(score=70.0)
    technical = TechnicalAnalysis(score=75.0)
    sentiment = SentimentAnalysis(score=80.0)

    score, rec, confidence = screener._calculate_recommendation(fundamental, technical, sentiment)

    assert 0 <= score <= 100
    assert rec in ("BUY", "HOLD", "SELL")
    assert 0 <= confidence <= 1
    # With scores 70/75/80 the weighted average should be in the BUY range
    assert rec == "BUY", f"Expected BUY for high scores, got {rec}"


def test_calculate_recommendation_none_sentiment(screener):
    """_calculate_recommendation must handle None sentiment gracefully."""
    fundamental = FundamentalAnalysis(score=60.0)
    technical = TechnicalAnalysis(score=55.0)

    score, rec, confidence = screener._calculate_recommendation(fundamental, technical, None)

    assert 0 <= score <= 100
    assert rec in ("BUY", "HOLD", "SELL")


# ---------------------------------------------------------------------------
# Improvement 3: screen_stocks must run analyses concurrently
# ---------------------------------------------------------------------------

def test_screen_stocks_concurrent(screener):
    """screen_stocks must return results for all symbols even when run concurrently."""
    symbols = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"]

    with patch.object(screener, "analyze_stock", side_effect=_make_analysis) as mock_analyze:
        result = screener.screen_stocks(symbols, ScreeningFilter(min_overall_score=0))

    assert mock_analyze.call_count == len(symbols)
    assert result.total_candidates == len(symbols)
    assert result.filtered_count == len(symbols)
    assert len(result.top_picks) == len(symbols)


def test_screen_stocks_filters_applied_concurrently(screener):
    """Filters must still be applied correctly when analysis runs concurrently."""
    def _analyze(symbol: str):
        score = 80.0 if symbol in ("AAPL", "MSFT") else 40.0
        return _make_analysis(symbol, overall_score=score)

    symbols = ["AAPL", "MSFT", "GOOGL"]

    with patch.object(screener, "analyze_stock", side_effect=_analyze):
        result = screener.screen_stocks(symbols, ScreeningFilter(min_overall_score=60))

    assert result.filtered_count == 2
    returned_symbols = {s.symbol for s in result.top_picks}
    assert returned_symbols == {"AAPL", "MSFT"}


def test_screen_stocks_handles_analysis_failure(screener):
    """screen_stocks must not crash when analyze_stock returns None for a symbol."""
    def _analyze(symbol: str):
        return None if symbol == "FAIL" else _make_analysis(symbol)

    symbols = ["AAPL", "FAIL", "MSFT"]

    with patch.object(screener, "analyze_stock", side_effect=_analyze):
        result = screener.screen_stocks(symbols, ScreeningFilter(min_overall_score=0))

    assert result.total_candidates == 3
    assert result.filtered_count == 2
