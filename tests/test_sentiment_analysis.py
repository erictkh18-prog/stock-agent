"""Test suite for sentiment analysis"""
import calendar
import pytest
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock
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

def test_get_feed_sentiment_date_parsing(analyzer, monkeypatch):
    """_get_feed_sentiment must use calendar.timegm for correct UTC date conversion.

    The implementation uses ``calendar.timegm(entry.published_parsed)`` with a
    timezone-aware ``datetime`` so that only entries within the look-back window
    are included. This test verifies that entries published "now" pass the filter.
    """
    # Build a fake feed entry whose published_parsed is exactly "now" in UTC
    now_utc_struct = time.gmtime()
    entry = MagicMock()
    entry.published_parsed = now_utc_struct
    entry.title = "Stock surges higher"
    entry.summary = "Great earnings"
    entry.get = MagicMock(side_effect=lambda key, default="": {"title": entry.title, "summary": entry.summary}.get(key, default))

    fake_feed = MagicMock()
    fake_feed.entries = [entry]

    monkeypatch.setattr("feedparser.parse", lambda url: fake_feed)

    sentiments = analyzer._get_feed_sentiment("http://fake", lookback_days=7)
    # The entry is from "now", so it must not be filtered out
    assert len(sentiments) == 1, (
        "Entry published now should survive the lookback filter when dates are parsed correctly"
    )

    # Also verify the conversion itself: calendar.timegm round-trips correctly
    ts = calendar.timegm(now_utc_struct)
    reconstructed = datetime.fromtimestamp(ts, tz=timezone.utc)
    assert abs((reconstructed - datetime.now(timezone.utc)).total_seconds()) < 5, (
        "calendar.timegm should produce a timestamp within a few seconds of now"
    )
