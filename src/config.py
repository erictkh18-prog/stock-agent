import os
from dotenv import load_dotenv
from typing import Optional

load_dotenv()

class Config:
    """Configuration settings for the stock analysis agent"""
    
    # API Configuration
    ALPHA_VANTAGE_KEY: Optional[str] = os.getenv("ALPHA_VANTAGE_KEY")
    NEWSAPI_KEY: Optional[str] = os.getenv("NEWSAPI_KEY")
    
    # Application Settings
    DEBUG: bool = os.getenv("DEBUG", "False").lower() == "true"
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    
    # Analysis Parameters
    FUNDAMENTAL_LOOKBACK_DAYS: int = 365 * 2  # 2 years
    TECHNICAL_LOOKBACK_DAYS: int = 365  # 1 year for technical analysis
    SENTIMENT_LOOKBACK_DAYS: int = 30  # 1 month for sentiment
    
    # Screening Parameters
    MIN_MARKET_CAP: int = 1_000_000_000  # 1 billion
    MIN_VOLUME: int = 1_000_000  # 1 million shares
    
    # Cache Settings
    CACHE_TTL_SECONDS: int = 3600  # 1 hour

    # Concurrency & Timeout Settings
    MAX_CONCURRENT_FETCHES: int = int(os.getenv("MAX_CONCURRENT_FETCHES", "10"))
    SYMBOL_FETCH_TIMEOUT_SECONDS: int = int(os.getenv("SYMBOL_FETCH_TIMEOUT_SECONDS", "30"))

config = Config()
