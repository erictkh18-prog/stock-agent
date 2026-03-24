"""Sentiment analysis module for stocks"""
import requests
import feedparser
import logging
from typing import Optional, List, Tuple
from datetime import datetime, timedelta
from textblob import TextBlob

logger = logging.getLogger(__name__)

class SentimentAnalyzer:
    """Analyzes sentiment for stocks from news and social media"""
    
    def __init__(self, newsapi_key: Optional[str] = None):
        self.newsapi_key = newsapi_key
        self.logger = logger
    
    def analyze(self, symbol: str, lookback_days: int = 30) -> dict:
        """
        Analyze sentiment for a stock
        
        Args:
            symbol: Stock ticker symbol
            lookback_days: Days to look back for news
        
        Returns:
            Dictionary with sentiment analysis
        """
        result = {
            'news_sentiment': None,
            'news_count': 0,
            'analyst_sentiment': 'neutral',
            'institutional_ownership': None,
            'score': 50
        }
        
        try:
            # Fetch news and calculate sentiment
            news_sentiment, news_count = self._analyze_news_sentiment(symbol, lookback_days)
            
            if news_sentiment is not None:
                result['news_sentiment'] = news_sentiment
                result['news_count'] = news_count
            
            # Determine analyst sentiment based on news
            analyst_sentiment = self._determine_analyst_sentiment(news_sentiment)
            result['analyst_sentiment'] = analyst_sentiment
            
            # Calculate score
            score = self._calculate_sentiment_score(news_sentiment, analyst_sentiment)
            result['score'] = score
        
        except Exception as e:
            self.logger.error(f"Error analyzing sentiment for {symbol}: {e}")
        
        return result
    
    def _analyze_news_sentiment(self, symbol: str, lookback_days: int) -> Tuple[Optional[float], int]:
        """
        Analyze sentiment from news articles
        
        Returns:
            Tuple of (average_sentiment, article_count) where sentiment is -1 to 1
        """
        try:
            # Try to get news from free RSS feeds (Yahoo Finance, etc.)
            sentiments = []
            
            # Yahoo Finance news feed
            feed_url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}"
            sentiments.extend(self._get_feed_sentiment(feed_url, lookback_days))
            
            # Simple calculation based on collected sentiment
            if sentiments:
                avg_sentiment = sum(sentiments) / len(sentiments)
                return avg_sentiment, len(sentiments)
            
            return None, 0
        
        except Exception as e:
            self.logger.warning(f"Error fetching news sentiment for {symbol}: {e}")
            return None, 0
    
    def _get_feed_sentiment(self, feed_url: str, lookback_days: int) -> List[float]:
        """Extract sentiment from RSS feed"""
        sentiments = []
        
        try:
            feed = feedparser.parse(feed_url)
            cutoff_date = datetime.now() - timedelta(days=lookback_days)
            
            for entry in feed.entries[:20]:  # Limit to 20 entries
                try:
                    # Parse entry date if available
                    if hasattr(entry, 'published_parsed') and entry.published_parsed:
                        entry_date = datetime.fromtimestamp(
                            entry.published_parsed[0:9].__hash__() % 2000000000
                        )
                    else:
                        entry_date = datetime.now()
                    
                    # Skip old entries
                    if entry_date < cutoff_date:
                        continue
                    
                    # Analyze title and summary
                    text = entry.get('title', '') + ' ' + entry.get('summary', '')
                    sentiment = self._calculate_text_sentiment(text)
                    sentiments.append(sentiment)
                
                except Exception as e:
                    self.logger.debug(f"Error processing feed entry: {e}")
                    continue
        
        except Exception as e:
            self.logger.warning(f"Error parsing feed: {e}")
        
        return sentiments
    
    def _calculate_text_sentiment(self, text: str) -> float:
        """
        Calculate sentiment of text using TextBlob
        
        Returns:
            Sentiment score from -1 to 1
        """
        try:
            blob = TextBlob(text)
            polarity = blob.sentiment.polarity  # -1 to 1
            return polarity
        except Exception:
            return 0.0
    
    def _determine_analyst_sentiment(self, news_sentiment: Optional[float]) -> str:
        """
        Determine analyst sentiment based on sentiment score
        
        Args:
            news_sentiment: Average sentiment from -1 to 1
        
        Returns:
            'bullish', 'neutral', or 'bearish'
        """
        if news_sentiment is None:
            return 'neutral'
        
        if news_sentiment > 0.1:
            return 'bullish'
        elif news_sentiment < -0.1:
            return 'bearish'
        else:
            return 'neutral'
    
    def _calculate_sentiment_score(
        self, news_sentiment: Optional[float], analyst_sentiment: str
    ) -> float:
        """
        Calculate sentiment score (0-100)
        
        Scoring logic:
        - Bullish sentiment: 70+
        - Neutral sentiment: 50
        - Bearish sentiment: 30-
        """
        score = 50
        
        # Analyst sentiment
        if analyst_sentiment == 'bullish':
            score = 70
        elif analyst_sentiment == 'bearish':
            score = 30
        
        # Fine-tune with news sentiment
        if news_sentiment is not None:
            score = int(50 + (news_sentiment * 50))
        
        return max(0, min(100, score))
