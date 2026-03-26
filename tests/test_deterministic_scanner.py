"""Tests for deterministic US Market Scanner behaviour."""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

from src.stock_screener import StockScreener
from src.models import (
    ScreeningFilter,
    StockAnalysis,
    FundamentalAnalysis,
    TechnicalAnalysis,
    SentimentAnalysis,
)


def _make_analysis(symbol: str, score: float) -> StockAnalysis:
    """Create a minimal StockAnalysis fixture with a given overall score."""
    return StockAnalysis(
        symbol=symbol,
        name=f"{symbol} Inc.",
        current_price=100.0,
        timestamp=datetime.now(),
        fundamental=FundamentalAnalysis(score=score),
        technical=TechnicalAnalysis(score=score),
        sentiment=SentimentAnalysis(score=score),
        overall_score=score,
        recommendation="BUY" if score >= 70 else "HOLD",
        confidence=0.8,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_screener_with_fixed_analyses(analyses: list[StockAnalysis]) -> StockScreener:
    """
    Return a StockScreener whose analyze_stock() returns analyses from the
    supplied list, matched by symbol.
    """
    lookup = {a.symbol: a for a in analyses}
    screener = StockScreener()
    screener.analyze_stock = lambda sym: lookup.get(sym)
    return screener


# ---------------------------------------------------------------------------
# Deterministic-mode tests
# ---------------------------------------------------------------------------

class TestDeterministicMode:
    """screen_stocks() with seed should produce stable, reproducible results."""

    def _symbols_and_analyses(self):
        symbols = ["MSFT", "AAPL", "NVDA", "GOOGL", "AMZN", "META", "TSLA"]
        # Give each stock a distinct score to avoid reliance on tie-breaking.
        analyses = [
            _make_analysis("MSFT", 88.0),
            _make_analysis("AAPL", 85.0),
            _make_analysis("NVDA", 82.0),
            _make_analysis("GOOGL", 79.0),
            _make_analysis("AMZN", 75.0),
            _make_analysis("META", 72.0),
            _make_analysis("TSLA", 68.0),
        ]
        return symbols, analyses

    def test_stable_order_across_runs(self):
        """Same seed ⇒ identical top-picks order on repeated calls."""
        symbols, analyses = self._symbols_and_analyses()
        screener = _build_screener_with_fixed_analyses(analyses)
        filters = ScreeningFilter(min_overall_score=60)

        result1 = screener.screen_stocks(symbols, filters, top_n=5, seed=42)
        result2 = screener.screen_stocks(symbols, filters, top_n=5, seed=42)

        assert [s.symbol for s in result1.top_picks] == [s.symbol for s in result2.top_picks]

    def test_deterministic_mode_flag_set(self):
        """ScreeningResult.deterministic_mode must be True when seed is given."""
        symbols, analyses = self._symbols_and_analyses()
        screener = _build_screener_with_fixed_analyses(analyses)
        result = screener.screen_stocks(symbols, ScreeningFilter(), top_n=5, seed=1)

        assert result.deterministic_mode is True
        assert result.seed == 1

    def test_seed_value_preserved(self):
        """The seed provided by the caller must be echoed back in the result."""
        symbols, analyses = self._symbols_and_analyses()
        screener = _build_screener_with_fixed_analyses(analyses)
        for seed_val in (0, 99, 12345):
            result = screener.screen_stocks(symbols, ScreeningFilter(), seed=seed_val)
            assert result.seed == seed_val

    def test_results_sorted_by_score_descending(self):
        """Top picks must be ordered highest-score first in deterministic mode."""
        symbols, analyses = self._symbols_and_analyses()
        screener = _build_screener_with_fixed_analyses(analyses)
        result = screener.screen_stocks(symbols, ScreeningFilter(min_overall_score=0), top_n=7, seed=42)

        scores = [p.overall_score for p in result.top_picks]
        assert scores == sorted(scores, reverse=True)

    def test_tie_breaking_is_stable_for_same_seed(self):
        """Tied scores should keep the same order when the same seed is reused."""
        symbols = ["ZZZ", "AAA", "MMM"]
        analyses = [
            _make_analysis("ZZZ", 75.0),
            _make_analysis("AAA", 75.0),
            _make_analysis("MMM", 75.0),
        ]
        screener = _build_screener_with_fixed_analyses(analyses)
        result_a = screener.screen_stocks(symbols, ScreeningFilter(min_overall_score=0), top_n=3, seed=1)
        result_b = screener.screen_stocks(symbols, ScreeningFilter(min_overall_score=0), top_n=3, seed=1)

        assert [p.symbol for p in result_a.top_picks] == [p.symbol for p in result_b.top_picks]

    def test_different_seeds_can_change_tied_order(self):
        """Different seeds should be able to change ordering when scores are tied."""
        symbols = ["ZZZ", "AAA", "MMM"]
        analyses = [
            _make_analysis("ZZZ", 75.0),
            _make_analysis("AAA", 75.0),
            _make_analysis("MMM", 75.0),
        ]
        screener = _build_screener_with_fixed_analyses(analyses)
        filters = ScreeningFilter(min_overall_score=0)

        result_a = screener.screen_stocks(symbols, filters, top_n=7, seed=1)
        result_b = screener.screen_stocks(symbols, filters, top_n=7, seed=999)

        assert [p.symbol for p in result_a.top_picks] != [p.symbol for p in result_b.top_picks]


# ---------------------------------------------------------------------------
# Non-deterministic mode (default) tests
# ---------------------------------------------------------------------------

class TestNonDeterministicMode:
    """screen_stocks() without seed should preserve existing behaviour."""

    def test_no_seed_deterministic_mode_false(self):
        """ScreeningResult.deterministic_mode must be False when no seed is given."""
        symbols = ["AAPL", "MSFT"]
        analyses = [_make_analysis("AAPL", 80.0), _make_analysis("MSFT", 75.0)]
        screener = _build_screener_with_fixed_analyses(analyses)

        result = screener.screen_stocks(symbols, ScreeningFilter(min_overall_score=0))

        assert result.deterministic_mode is False
        assert result.seed is None

    def test_no_seed_returns_results(self):
        """Non-deterministic mode still returns scored results."""
        symbols = ["AAPL", "MSFT", "GOOGL"]
        analyses = [
            _make_analysis("AAPL", 80.0),
            _make_analysis("MSFT", 75.0),
            _make_analysis("GOOGL", 70.0),
        ]
        screener = _build_screener_with_fixed_analyses(analyses)
        result = screener.screen_stocks(symbols, ScreeningFilter(min_overall_score=60), top_n=3)

        assert len(result.top_picks) == 3
        assert result.total_candidates == 3

    def test_no_seed_results_sorted_by_score(self):
        """Results must still be ordered by score even without a seed."""
        symbols = ["TSLA", "AAPL", "MSFT"]
        analyses = [
            _make_analysis("TSLA", 65.0),
            _make_analysis("AAPL", 85.0),
            _make_analysis("MSFT", 75.0),
        ]
        screener = _build_screener_with_fixed_analyses(analyses)
        result = screener.screen_stocks(symbols, ScreeningFilter(min_overall_score=0), top_n=3)

        scores = [p.overall_score for p in result.top_picks]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# ScreeningResult model tests
# ---------------------------------------------------------------------------

class TestScreeningResultModel:
    """ScreeningResult model has the expected deterministic metadata fields."""

    def test_default_values(self):
        from src.models import ScreeningResult
        result = ScreeningResult(
            total_candidates=10,
            filtered_count=5,
            top_picks=[],
            screening_timestamp=datetime.now(),
        )
        assert result.deterministic_mode is False
        assert result.seed is None

    def test_deterministic_fields_serialize(self):
        from src.models import ScreeningResult
        result = ScreeningResult(
            total_candidates=10,
            filtered_count=5,
            top_picks=[],
            screening_timestamp=datetime.now(),
            deterministic_mode=True,
            seed=77,
        )
        data = result.model_dump()
        assert data["deterministic_mode"] is True
        assert data["seed"] == 77
