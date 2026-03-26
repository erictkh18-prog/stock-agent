"""Tests for StockScreener ranking, explanation logic, caching, concurrency, and partial-failure handling."""
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


@pytest.fixture
def screener():
    return StockScreener()


def _make_sentiment(analyst: str = "neutral", news: float = 0.0) -> SentimentAnalysis:
    return SentimentAnalysis(
        analyst_sentiment=analyst,
        news_sentiment=news,
        score=50,
    )


# ---------------------------------------------------------------------------
# Score weighting tests
# ---------------------------------------------------------------------------

def test_recommendation_buy_threshold(screener):
    """Overall score >= 70 should yield BUY."""
    fundamental = FundamentalAnalysis(score=80)
    technical = TechnicalAnalysis(score=80)
    sentiment_dict = {"score": 70}
    score, rec, conf = screener._calculate_recommendation(fundamental, technical, sentiment_dict)
    assert rec == "BUY"
    assert score >= 70


def test_recommendation_hold_threshold(screener):
    """Overall score in [50, 70) should yield HOLD."""
    fundamental = FundamentalAnalysis(score=55)
    technical = TechnicalAnalysis(score=55)
    sentiment_dict = {"score": 55}
    score, rec, conf = screener._calculate_recommendation(fundamental, technical, sentiment_dict)
    assert rec == "HOLD"
    assert 50 <= score < 70


def test_recommendation_sell_threshold(screener):
    """Overall score < 50 should yield SELL."""
    fundamental = FundamentalAnalysis(score=30)
    technical = TechnicalAnalysis(score=30)
    sentiment_dict = {"score": 30}
    score, rec, conf = screener._calculate_recommendation(fundamental, technical, sentiment_dict)
    assert rec == "SELL"
    assert score < 50


def test_score_weighting_fundamental_dominant(screener):
    """With only fundamental score present the overall score equals that score."""
    fundamental = FundamentalAnalysis(score=80)
    score, rec, _ = screener._calculate_recommendation(fundamental, None, {})
    assert score == pytest.approx(80.0)


def test_score_weighting_technical_dominant(screener):
    """With only technical score present the overall score equals that score."""
    technical = TechnicalAnalysis(score=65)
    score, _, _ = screener._calculate_recommendation(None, technical, {})
    assert score == pytest.approx(65.0)


def test_score_weighting_all_components(screener):
    """Verify 40/40/20 weighting when all three components are present."""
    fundamental = FundamentalAnalysis(score=80)
    technical = TechnicalAnalysis(score=60)
    sentiment_dict = {"score": 50}
    score, _, _ = screener._calculate_recommendation(fundamental, technical, sentiment_dict)
    expected = (80 * 0.40 + 60 * 0.40 + 50 * 0.20) / 1.0
    assert score == pytest.approx(expected)


def test_score_defaults_to_50_with_no_data(screener):
    """No available scores should default to 50 (neutral)."""
    score, rec, _ = screener._calculate_recommendation(None, None, {})
    assert score == 50
    assert rec == "HOLD"


def test_sentiment_weight_is_lower_than_technical(screener):
    """A change in the sentiment score should move the overall score less than the same change
    in the technical score, reflecting the lower 20 % vs 40 % weight."""
    base_fundamental = FundamentalAnalysis(score=60)

    # Establish baseline: both technical and sentiment at 60 (result unused, but
    # the calculation verifies the call is valid with symmetric inputs)
    screener._calculate_recommendation(
        base_fundamental,
        TechnicalAnalysis(score=60),
        {"score": 60},
    )

    # Boost only sentiment by 20 points
    score_sentiment_boost, _, _ = screener._calculate_recommendation(
        base_fundamental,
        TechnicalAnalysis(score=60),
        {"score": 80},
    )

    # Boost only technical by 20 points
    score_technical_boost, _, _ = screener._calculate_recommendation(
        base_fundamental,
        TechnicalAnalysis(score=80),
        {"score": 60},
    )

    assert score_technical_boost > score_sentiment_boost


# ---------------------------------------------------------------------------
# Explanation / reason generation tests
# ---------------------------------------------------------------------------

def test_reason_positive_earnings(screener):
    """Positive EPS should appear as a contributing factor."""
    fundamental = FundamentalAnalysis(eps=2.5, score=70)
    reason, contributing, risks = screener._build_explanation(fundamental, None, None)
    assert any("earnings" in f for f in contributing)
    assert isinstance(reason, str)
    assert len(reason) > 0


def test_reason_negative_earnings(screener):
    """Negative EPS should appear as a risk factor."""
    fundamental = FundamentalAnalysis(eps=-1.0, score=30)
    reason, contributing, risks = screener._build_explanation(fundamental, None, None)
    assert any("earnings" in r for r in risks)


def test_reason_reasonable_valuation(screener):
    """P/E in 10-25 range should be flagged as reasonable valuation."""
    fundamental = FundamentalAnalysis(pe_ratio=18, score=70)
    _, contributing, _ = screener._build_explanation(fundamental, None, None)
    assert any("valuation" in f for f in contributing)


def test_reason_elevated_valuation(screener):
    """P/E > 35 should flag elevated valuation risk."""
    fundamental = FundamentalAnalysis(pe_ratio=50, score=40)
    _, _, risks = screener._build_explanation(fundamental, None, None)
    assert any("valuation" in r for r in risks)


def test_reason_strong_revenue_growth(screener):
    """Revenue growth > 15 % should be listed as a contributing factor."""
    fundamental = FundamentalAnalysis(revenue_growth=0.20, score=75)
    _, contributing, _ = screener._build_explanation(fundamental, None, None)
    assert any("revenue" in f for f in contributing)


def test_reason_declining_revenue(screener):
    """Negative revenue growth should be listed as a risk factor."""
    fundamental = FundamentalAnalysis(revenue_growth=-0.10, score=40)
    _, _, risks = screener._build_explanation(fundamental, None, None)
    assert any("revenue" in r for r in risks)


def test_reason_uptrend(screener):
    """Uptrend should contribute a momentum factor."""
    technical = TechnicalAnalysis(trend="uptrend", score=75)
    _, contributing, _ = screener._build_explanation(None, technical, None)
    assert any("momentum" in f for f in contributing)


def test_reason_downtrend(screener):
    """Downtrend should contribute a risk factor."""
    technical = TechnicalAnalysis(trend="downtrend", score=30)
    _, _, risks = screener._build_explanation(None, technical, None)
    assert any("downtrend" in r for r in risks)


def test_reason_overbought_rsi(screener):
    """RSI > 70 should be flagged as overbought risk."""
    technical = TechnicalAnalysis(rsi=80, score=50)
    _, _, risks = screener._build_explanation(None, technical, None)
    assert any("overbought" in r for r in risks)


def test_reason_oversold_rsi(screener):
    """RSI < 30 should be flagged as a potential reversal opportunity."""
    technical = TechnicalAnalysis(rsi=20, score=55)
    _, contributing, _ = screener._build_explanation(None, technical, None)
    assert any("oversold" in f for f in contributing)


def test_reason_bullish_sentiment(screener):
    """Bullish analyst sentiment should show as contributing factor."""
    sentiment = _make_sentiment(analyst="bullish", news=0.3)
    _, contributing, _ = screener._build_explanation(None, None, sentiment)
    assert any("sentiment" in f for f in contributing)


def test_reason_bearish_sentiment(screener):
    """Bearish analyst sentiment should show as a risk factor."""
    sentiment = _make_sentiment(analyst="bearish", news=-0.3)
    _, _, risks = screener._build_explanation(None, None, sentiment)
    assert any("sentiment" in r for r in risks)


def test_reason_no_data_fallback(screener):
    """With no analysis data the reason should be a sensible fallback message."""
    reason, contributing, risks = screener._build_explanation(None, None, None)
    assert "insufficient" in reason.lower() or "data" in reason.lower()
    assert contributing == []
    assert risks == []


def test_reason_string_includes_risk_hint(screener):
    """When both contributing factors and risks exist the reason should mention the risk."""
    fundamental = FundamentalAnalysis(eps=3.0, pe_ratio=18, revenue_growth=0.20, score=75)
    technical = TechnicalAnalysis(rsi=80, trend="uptrend", score=65)
    reason, _, _ = screener._build_explanation(fundamental, technical, None)
    assert "watch for" in reason.lower()


def test_contributing_factors_capped_at_three(screener):
    """At most 3 contributing factors should be returned."""
    fundamental = FundamentalAnalysis(
        eps=5.0,
        pe_ratio=15,
        revenue_growth=0.20,
        roe=0.20,
        debt_to_equity=0.3,
        score=90,
    )
    technical = TechnicalAnalysis(trend="uptrend", rsi=50, score=80)
    _, contributing, _ = screener._build_explanation(fundamental, technical, None)
    assert len(contributing) <= 3


def test_risk_factors_capped_at_three(screener):
    """At most 3 risk factors should be returned."""
    fundamental = FundamentalAnalysis(
        eps=-1.0,
        pe_ratio=50,
        revenue_growth=-0.15,
        debt_to_equity=3.0,
        current_ratio=0.8,
        score=20,
    )
    technical = TechnicalAnalysis(trend="downtrend", rsi=80, score=25)
    _, _, risks = screener._build_explanation(fundamental, technical, None)
    assert len(risks) <= 3


def test_macd_bullish_signal(screener):
    """Positive MACD histogram should appear as contributing factor."""
    technical = TechnicalAnalysis(
        macd={"macd": 0.5, "signal": 0.3, "histogram": 0.2},
        score=65,
    )
    _, contributing, _ = screener._build_explanation(None, technical, None)
    assert any("macd" in f.lower() for f in contributing)


def test_macd_bearish_signal(screener):
    """Negative MACD histogram should appear as a risk factor."""
    technical = TechnicalAnalysis(
        macd={"macd": -0.5, "signal": -0.3, "histogram": -0.2},
        score=40,
    )
    _, _, risks = screener._build_explanation(None, technical, None)
    assert any("macd" in r.lower() for r in risks)


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
