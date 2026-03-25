"""Tests for StockScreener ranking and explanation logic."""
import pytest
from src.stock_screener import StockScreener
from src.models import FundamentalAnalysis, TechnicalAnalysis, SentimentAnalysis


@pytest.fixture
def screener():
    return StockScreener()


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

def _make_sentiment(analyst: str = "neutral", news: float = 0.0) -> SentimentAnalysis:
    return SentimentAnalysis(
        analyst_sentiment=analyst,
        news_sentiment=news,
        score=50,
    )


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
