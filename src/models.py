from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

class FundamentalAnalysis(BaseModel):
    """Fundamental analysis metrics"""
    pe_ratio: Optional[float] = None
    eps: Optional[float] = None
    dividend_yield: Optional[float] = None
    debt_to_equity: Optional[float] = None
    current_ratio: Optional[float] = None
    roa: Optional[float] = None  # Return on assets
    roe: Optional[float] = None  # Return on equity
    revenue_growth: Optional[float] = None
    profit_margin: Optional[float] = None
    peg_ratio: Optional[float] = None
    score: Optional[float] = Field(None, description="Score 0-100")

class TechnicalAnalysis(BaseModel):
    """Technical analysis metrics"""
    sma_50: Optional[float] = None  # 50-day simple moving average
    sma_200: Optional[float] = None  # 200-day simple moving average
    rsi: Optional[float] = None  # Relative Strength Index (0-100)
    macd: Optional[Dict[str, float]] = None  # MACD values
    bollinger_bands: Optional[Dict[str, float]] = None  # Upper, middle, lower
    support_level: Optional[float] = None
    resistance_level: Optional[float] = None
    trend: Optional[str] = None  # uptrend, downtrend, sideways
    score: Optional[float] = Field(None, description="Score 0-100")

class SentimentAnalysis(BaseModel):
    """Sentiment analysis metrics"""
    news_sentiment: Optional[float] = Field(None, description="Average sentiment -1 to 1")
    news_count: Optional[int] = None
    analyst_sentiment: Optional[str] = None  # bullish, neutral, bearish
    institutional_ownership: Optional[float] = None
    score: Optional[float] = Field(None, description="Score 0-100")

class StockAnalysis(BaseModel):
    """Complete stock analysis"""
    symbol: str
    name: str
    current_price: float
    currency: str = "USD"
    timestamp: datetime
    fundamental: Optional[FundamentalAnalysis] = None
    technical: Optional[TechnicalAnalysis] = None
    sentiment: Optional[SentimentAnalysis] = None
    overall_score: float = Field(description="Overall score 0-100")
    recommendation: str = Field(description="BUY, HOLD, SELL")
    confidence: float = Field(description="Confidence level 0-1")
    reason: Optional[str] = Field(None, description="Plain-language summary of key ranking factors")
    top_contributing_factors: Optional[List[str]] = Field(None, description="Top positive factors")
    top_risk_factors: Optional[List[str]] = Field(None, description="Top risk factors")

class ScreeningFilter(BaseModel):
    """Stock screening filters"""
    min_pe_ratio: Optional[float] = None
    max_pe_ratio: Optional[float] = None
    min_dividend_yield: Optional[float] = None
    max_debt_to_equity: Optional[float] = None
    min_revenue_growth: Optional[float] = None
    min_market_cap: Optional[float] = None
    min_volume: Optional[float] = None
    trend: Optional[str] = None  # uptrend, downtrend
    min_overall_score: float = 60

class ScreeningResult(BaseModel):
    """Stock screening results"""
    total_candidates: int
    filtered_count: int
    top_picks: List[StockAnalysis]
    screening_timestamp: datetime
    deterministic_mode: bool = Field(False, description="True when a seed was supplied for stable ordering")
    seed: Optional[int] = Field(None, description="Seed value used for deterministic ordering")
