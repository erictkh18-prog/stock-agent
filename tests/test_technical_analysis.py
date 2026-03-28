"""Test suite for technical analysis"""
import pytest
import pandas as pd
from src.technical_analysis import TechnicalAnalyzer

@pytest.fixture
def analyzer():
    return TechnicalAnalyzer()

@pytest.fixture
def sample_data():
    """Create sample OHLC data"""
    dates = pd.date_range('2023-01-01', periods=100)
    data = pd.DataFrame({
        'Open': [100 + i * 0.5 for i in range(100)],
        'High': [101 + i * 0.5 for i in range(100)],
        'Low': [99 + i * 0.5 for i in range(100)],
        'Close': [100.5 + i * 0.5 for i in range(100)],
        'Volume': [1000000] * 100
    }, index=dates)
    return data

def test_calculate_sma(analyzer, sample_data):
    """Test SMA calculation"""
    sma_50 = analyzer._calculate_sma(sample_data, 50)
    assert sma_50 is not None
    assert isinstance(sma_50, float)

def test_determine_trend(analyzer):
    """Test trend determination"""
    trend = analyzer._determine_trend(sma_50=120, sma_200=100, current_price=125)
    assert trend == "uptrend"
    
    trend = analyzer._determine_trend(sma_50=80, sma_200=100, current_price=75)
    assert trend == "downtrend"
    
    trend = analyzer._determine_trend(sma_50=None, sma_200=None, current_price=100)
    assert trend == "unknown"


@pytest.fixture
def sample_data_200():
    """Create sample OHLC data with 260 rows (1+ year) for 52w and momentum tests."""
    import numpy as np
    n = 260
    dates = pd.date_range('2023-01-01', periods=n)
    # Trending upward with some volatility
    rng = np.random.default_rng(42)
    prices = 100 + np.cumsum(rng.normal(0.1, 1.5, n))
    prices = np.maximum(prices, 1.0)
    data = pd.DataFrame({
        'Open': prices * 0.998,
        'High': prices * 1.01,
        'Low': prices * 0.99,
        'Close': prices,
        'Volume': rng.integers(500_000, 2_000_000, n).astype(float),
    }, index=dates)
    return data


def test_calculate_ema(analyzer, sample_data):
    """EMA should return a float for sufficient data."""
    ema = analyzer._calculate_ema(sample_data, 20)
    assert ema is not None
    assert isinstance(ema, float)


def test_calculate_atr(analyzer, sample_data):
    """ATR should return a positive float."""
    atr = analyzer._calculate_atr(sample_data, 14)
    assert atr is not None
    assert atr > 0


def test_calculate_volume_ratio(analyzer, sample_data):
    """Volume ratio should return a positive float."""
    ratio = analyzer._calculate_volume_ratio(sample_data, 20)
    assert ratio is not None
    assert ratio > 0


def test_calculate_price_change(analyzer, sample_data_200):
    """Price change % should return a float for sufficient data."""
    change = analyzer._calculate_price_change(sample_data_200, 21)
    assert change is not None
    assert isinstance(change, float)


def test_calculate_52w_high_low(analyzer, sample_data_200):
    """52-week high/low should be valid values."""
    high, low = analyzer._calculate_52w_high_low(sample_data_200)
    assert high is not None
    assert low is not None
    assert high >= low


def test_technical_score_includes_momentum(analyzer, sample_data_200):
    """Score should differ when price_change_3m is provided vs not."""
    sma50 = analyzer._calculate_sma(sample_data_200, 50)
    sma200 = analyzer._calculate_sma(sample_data_200, 200)
    rsi = analyzer._calculate_rsi(sample_data_200, 14)
    current = sample_data_200['Close'].iloc[-1]
    trend = analyzer._determine_trend(sma50, sma200, current)

    score_no_mom = analyzer._calculate_technical_score(sma50, sma200, rsi, trend, current, None, None)
    score_with_strong_mom = analyzer._calculate_technical_score(
        sma50, sma200, rsi, trend, current, None, None,
        price_change_3m=0.25
    )
    assert score_with_strong_mom > score_no_mom, "Strong 3m momentum should raise score"


def test_technical_score_volume_confirmation(analyzer, sample_data_200):
    """High volume ratio should increase score."""
    sma50 = analyzer._calculate_sma(sample_data_200, 50)
    sma200 = analyzer._calculate_sma(sample_data_200, 200)
    rsi = analyzer._calculate_rsi(sample_data_200, 14)
    current = sample_data_200['Close'].iloc[-1]
    trend = analyzer._determine_trend(sma50, sma200, current)

    score_no_vol = analyzer._calculate_technical_score(sma50, sma200, rsi, trend, current, None, None)
    score_high_vol = analyzer._calculate_technical_score(
        sma50, sma200, rsi, trend, current, None, None,
        volume_ratio=2.0
    )
    assert score_high_vol > score_no_vol, "High volume should raise score"


# ── Item 5: Breakout detection ──────────────────────────────────────────────────────

def test_breakout_raises_score(analyzer):
    """is_breakout=True should increase score vs baseline."""
    base = analyzer._calculate_technical_score(None, None, 55.0, "uptrend", 100.0, None, None)
    with_breakout = analyzer._calculate_technical_score(
        None, None, 55.0, "uptrend", 100.0, None, None, is_breakout=True
    )
    assert with_breakout > base, "Breakout flag should raise technical score"


def test_no_breakout_does_not_raise_score(analyzer):
    """is_breakout=False should not add breakout bonus."""
    base = analyzer._calculate_technical_score(None, None, 55.0, "uptrend", 100.0, None, None)
    no_breakout = analyzer._calculate_technical_score(
        None, None, 55.0, "uptrend", 100.0, None, None, is_breakout=False
    )
    assert no_breakout == base, "is_breakout=False should not change score vs None"


def test_breakout_detected_in_data(analyzer):
    """Construct data where current price is at 20-day high and volume is elevated."""
    import pandas as pd
    import numpy as np

    n = 100
    dates = pd.date_range('2024-01-01', periods=n)
    base_price = 100.0
    closes = [base_price] * n
    # Make last bar the highest close in the 20-day window
    closes[-1] = base_price + 1.0
    volumes = [1_000_000.0] * n
    # Spike volume on the last bar to 2.5x average
    volumes[-1] = 2_500_000.0
    hist = pd.DataFrame({
        'Open': closes, 'High': closes, 'Low': closes, 'Close': closes, 'Volume': volumes
    }, index=dates)

    volume_ratio = analyzer._calculate_volume_ratio(hist, 20)
    current_price = float(hist['Close'].iloc[-1])
    high_20d = float(hist['High'].tail(20).max())
    pct_from_20d_high = (current_price - high_20d) / high_20d

    # Verify the conditions that the analyzer checks
    assert pct_from_20d_high >= -0.03
    assert volume_ratio is not None
    assert volume_ratio >= 1.5


def test_relative_strength_vs_spy_raises_score(analyzer):
    """Stocks outperforming SPY by > 10% should get the maximum RS bonus."""
    base = analyzer._calculate_technical_score(None, None, 55.0, "uptrend", 100.0, None, None)
    with_rs = analyzer._calculate_technical_score(
        None, None, 55.0, "uptrend", 100.0, None, None,
        relative_strength_vs_spy=0.12
    )
    assert with_rs > base


def test_relative_strength_vs_spy_lowers_score_when_negative(analyzer):
    """Stocks lagging SPY by > 10% should be penalised."""
    base = analyzer._calculate_technical_score(None, None, 55.0, "uptrend", 100.0, None, None)
    lagging = analyzer._calculate_technical_score(
        None, None, 55.0, "uptrend", 100.0, None, None,
        relative_strength_vs_spy=-0.15
    )
    assert lagging < base
