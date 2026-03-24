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
