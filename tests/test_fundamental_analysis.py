"""Test suite for fundamental analysis"""
import pytest
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
