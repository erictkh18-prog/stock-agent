"""Tests for StockScreener caching, concurrency, and partial-failure handling."""
import time
from concurrent.futures import Future
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.models import (
    FundamentalAnalysis,
    ScreeningFilter,
    SentimentAnalysis,
    StockAnalysis,
    TechnicalAnalysis,
)
from src.stock_screener import StockScreener


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_analysis(symbol: str, score: float = 70.0) -> StockAnalysis:
    """Create a minimal StockAnalysis for testing."""
    return StockAnalysis(
        symbol=symbol,
        name=symbol,
        current_price=100.0,
        timestamp=datetime.now(),
        fundamental=FundamentalAnalysis(score=score),
        technical=TechnicalAnalysis(score=score),
        sentiment=SentimentAnalysis(score=score),
        overall_score=score,
        recommendation="BUY",
        confidence=0.8,
    )


# ---------------------------------------------------------------------------
# Cache hit / miss behaviour
# ---------------------------------------------------------------------------

class TestCacheBehavior:
    """Verify that repeated calls for the same symbol hit the cache."""

    def test_cache_hit_on_second_call(self):
        """Second call for the same symbol should return cached result."""
        screener = StockScreener()
        analysis = _make_analysis("AAPL")

        # Prime the cache manually
        with screener._cache_lock:
            screener.analysis_cache["AAPL"] = analysis

        # First call should be a cache hit
        result = screener._get_cached_analysis("AAPL")
        assert result is not None
        assert result.symbol == "AAPL"

        # Stats should reflect a hit via analyze_stock path
        with patch.object(screener, "_get_cached_analysis", return_value=analysis):
            returned = screener.analyze_stock("AAPL")
        assert returned is not None

    def test_cache_miss_on_first_call(self):
        """Empty cache should return None for any symbol."""
        screener = StockScreener()
        result = screener._get_cached_analysis("MSFT")
        assert result is None

    def test_cache_stats_increment(self):
        """analyze_stock should increment cache_hits when entry is warm."""
        screener = StockScreener()
        analysis = _make_analysis("TSLA")

        with screener._cache_lock:
            screener.analysis_cache["TSLA"] = analysis

        with patch.object(screener, "_get_cached_analysis", return_value=analysis) as mock_cache:
            screener.analyze_stock("TSLA")
            mock_cache.assert_called_once_with("TSLA")


# ---------------------------------------------------------------------------
# Cache expiration behaviour
# ---------------------------------------------------------------------------

class TestCacheExpiration:
    """Verify that expired cache entries are not returned."""

    def test_expired_entry_is_not_returned(self):
        """An entry older than cache_ttl_seconds must be treated as a miss."""
        screener = StockScreener()
        screener.cache_ttl_seconds = 60  # 1 minute TTL

        # Craft a stale analysis timestamped 2 minutes in the past
        stale_analysis = _make_analysis("NVDA")
        stale_analysis = stale_analysis.model_copy(
            update={"timestamp": datetime.now() - timedelta(seconds=121)}
        )
        with screener._cache_lock:
            screener.analysis_cache["NVDA"] = stale_analysis

        result = screener._get_cached_analysis("NVDA")
        assert result is None, "Expired entry should not be returned"

        # The stale entry should have been evicted from the cache
        with screener._cache_lock:
            assert "NVDA" not in screener.analysis_cache

    def test_fresh_entry_is_returned(self):
        """An entry within TTL must be returned unchanged."""
        screener = StockScreener()
        screener.cache_ttl_seconds = 3600

        fresh_analysis = _make_analysis("AMZN")
        with screener._cache_lock:
            screener.analysis_cache["AMZN"] = fresh_analysis

        result = screener._get_cached_analysis("AMZN")
        assert result is not None
        assert result.symbol == "AMZN"

    def test_ttl_boundary_exactly_expired(self):
        """An entry whose age equals TTL exactly is treated as expired."""
        screener = StockScreener()
        screener.cache_ttl_seconds = 300  # 5 minutes

        boundary_analysis = _make_analysis("GOOGL")
        # Age is exactly TTL + 1 second → expired
        boundary_analysis = boundary_analysis.model_copy(
            update={"timestamp": datetime.now() - timedelta(seconds=301)}
        )
        with screener._cache_lock:
            screener.analysis_cache["GOOGL"] = boundary_analysis

        result = screener._get_cached_analysis("GOOGL")
        assert result is None


# ---------------------------------------------------------------------------
# Partial failure handling
# ---------------------------------------------------------------------------

class TestPartialFailureHandling:
    """Verify that one failing symbol does not abort the whole scan."""

    def _make_screener_with_mock(self, symbol_map: dict) -> StockScreener:
        """Return a screener whose analyze_stock returns values from *symbol_map*.

        *symbol_map* maps ticker strings to either a :class:`StockAnalysis`
        instance (success case) or an :class:`Exception` instance (failure
        simulation).  Any symbol not present in the map will return ``None``.
        """
        screener = StockScreener()

        def _analyze(symbol):
            value = symbol_map.get(symbol)
            if isinstance(value, Exception):
                raise value
            return value

        screener.analyze_stock = MagicMock(side_effect=_analyze)
        return screener

    def test_one_failure_does_not_abort_scan(self):
        """screen_stocks must return results for healthy symbols even if one fails."""
        good_analysis = _make_analysis("AAPL", score=75)
        screener = self._make_screener_with_mock(
            {
                "AAPL": good_analysis,
                "BADFOO": Exception("simulated fetch error"),
            }
        )

        filters = ScreeningFilter(min_overall_score=0)
        result = screener.screen_stocks(["AAPL", "BADFOO"], filters, top_n=10)

        # Healthy symbol is present
        assert result.filtered_count >= 1
        assert any(p.symbol == "AAPL" for p in result.top_picks)

    def test_all_failures_returns_empty_result(self):
        """screen_stocks with all failing symbols must return an empty but valid result."""
        screener = self._make_screener_with_mock(
            {
                "BAD1": Exception("error"),
                "BAD2": Exception("error"),
            }
        )

        filters = ScreeningFilter(min_overall_score=0)
        result = screener.screen_stocks(["BAD1", "BAD2"], filters, top_n=10)

        assert result.total_candidates == 2
        assert result.filtered_count == 0
        assert result.top_picks == []

    def test_failed_symbols_tracked(self):
        """Symbols that raise exceptions must appear in failed_symbols."""
        good_analysis = _make_analysis("MSFT", score=80)

        # Patch _analyze_symbol_for_screen to simulate a mix of success and failure
        screener = StockScreener()

        def _side_effect(symbol, filters):
            if symbol == "MSFT":
                return good_analysis
            raise RuntimeError("simulated error")

        screener._analyze_symbol_for_screen = MagicMock(side_effect=_side_effect)

        filters = ScreeningFilter(min_overall_score=0)
        result = screener.screen_stocks(["MSFT", "BADFOO"], filters, top_n=10)

        assert "BADFOO" in result.failed_symbols
        assert "MSFT" not in result.failed_symbols

    def test_timeout_symbol_tracked(self):
        """A symbol whose future times out must appear in failed_symbols."""
        screener = StockScreener()
        screener.symbol_fetch_timeout_seconds = 1  # Very short timeout

        timeout_future: Future = Future()  # Never completes

        filters = ScreeningFilter(min_overall_score=0)

        with patch("src.stock_screener.ThreadPoolExecutor") as mock_executor_cls, \
             patch("src.stock_screener.as_completed") as mock_as_completed:

            mock_executor = MagicMock()
            mock_executor_cls.return_value.__enter__ = MagicMock(return_value=mock_executor)
            mock_executor_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_executor.submit = MagicMock(return_value=timeout_future)

            # as_completed yields the incomplete future
            mock_as_completed.return_value = iter([timeout_future])

            # future.result(timeout=1) on a never-completed future raises TimeoutError
            from concurrent.futures import TimeoutError as FuturesTimeoutError
            timeout_future.cancel()

            with patch.object(timeout_future, "result", side_effect=FuturesTimeoutError):
                result = screener.screen_stocks(["SLOWSYM"], filters, top_n=10)

        assert "SLOWSYM" in result.failed_symbols
        assert result.filtered_count == 0


# ---------------------------------------------------------------------------
# ScreeningResult metadata fields
# ---------------------------------------------------------------------------

class TestScreeningResultMetadata:
    """Verify the new metadata fields on ScreeningResult."""

    def test_scan_duration_ms_is_set(self):
        """screen_stocks must populate scan_duration_ms."""
        screener = StockScreener()
        analysis = _make_analysis("META", score=72)

        screener._analyze_symbol_for_screen = MagicMock(return_value=analysis)

        filters = ScreeningFilter(min_overall_score=0)
        result = screener.screen_stocks(["META"], filters, top_n=5)

        assert result.scan_duration_ms is not None
        assert result.scan_duration_ms >= 0

    def test_failed_symbols_empty_on_full_success(self):
        """failed_symbols must be empty when all symbols succeed."""
        screener = StockScreener()
        analysis = _make_analysis("V", score=65)

        screener._analyze_symbol_for_screen = MagicMock(return_value=analysis)

        filters = ScreeningFilter(min_overall_score=0)
        result = screener.screen_stocks(["V"], filters, top_n=5)

        assert result.failed_symbols == []

    def test_cache_hit_defaults_false(self):
        """ScreeningResult must default cache_hit to False."""
        from src.models import ScreeningResult
        result = ScreeningResult(
            total_candidates=0,
            filtered_count=0,
            top_picks=[],
            screening_timestamp=datetime.now(),
        )
        assert result.cache_hit is False
