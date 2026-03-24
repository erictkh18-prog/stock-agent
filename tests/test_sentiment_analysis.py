"""Test suite for sentiment analysis"""
import pytest
from src.sentiment_analysis import SentimentAnalyzer

@pytest.fixture
def analyzer():
    return SentimentAnalyzer()

def test_calculate_text_sentiment(analyzer):
    """Test text sentiment calculation"""
    positive_text = "This stock is excellent and has great potential"
    negative_text = "This stock is terrible and will lose money"
    
    pos_sentiment = analyzer._calculate_text_sentiment(positive_text)
    neg_sentiment = analyzer._calculate_text_sentiment(negative_text)
    
    assert pos_sentiment > 0, "Positive text should have positive sentiment"
    assert neg_sentiment < 0, "Negative text should have negative sentiment"

def test_determine_analyst_sentiment(analyzer):
    """Test analyst sentiment determination"""
    assert analyzer._determine_analyst_sentiment(0.5) == "bullish"
    assert analyzer._determine_analyst_sentiment(-0.5) == "bearish"
    assert analyzer._determine_analyst_sentiment(0.05) == "neutral"
    assert analyzer._determine_analyst_sentiment(None) == "neutral"

def test_calculate_sentiment_score(analyzer):
    """Test sentiment score calculation"""
    score_bullish = analyzer._calculate_sentiment_score(0.5, "bullish")
    score_bearish = analyzer._calculate_sentiment_score(-0.5, "bearish")
    
    assert 50 <= score_bullish <= 100, "Bullish should score high"
    assert 0 <= score_bearish <= 50, "Bearish should score low"
