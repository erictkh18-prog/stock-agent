from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

class FundamentalAnalysis(BaseModel):
    """Fundamental analysis metrics"""
    pe_ratio: Optional[float] = None
    forward_pe: Optional[float] = None            # Forward price-to-earnings ratio
    eps: Optional[float] = None
    eps_growth: Optional[float] = None            # YoY EPS growth rate
    dividend_yield: Optional[float] = None
    debt_to_equity: Optional[float] = None
    current_ratio: Optional[float] = None
    quick_ratio: Optional[float] = None           # (Current assets - inventory) / current liabilities
    roa: Optional[float] = None  # Return on assets
    roe: Optional[float] = None  # Return on equity
    revenue_growth: Optional[float] = None
    profit_margin: Optional[float] = None
    operating_margin: Optional[float] = None      # Operating income / revenue
    peg_ratio: Optional[float] = None
    pb_ratio: Optional[float] = None              # Price-to-book ratio
    price_to_sales: Optional[float] = None        # Price-to-sales ratio
    ev_ebitda: Optional[float] = None             # Enterprise value / EBITDA
    free_cash_flow: Optional[float] = None        # Free cash flow (absolute)
    fcf_yield: Optional[float] = None             # FCF / market cap
    beta: Optional[float] = None                  # Volatility relative to market
    roic: Optional[float] = None                  # Return on Invested Capital (net income / invested capital)
    eps_acceleration: Optional[float] = None      # QoQ EPS acceleration (positive = improving momentum)
    fcf_conversion: Optional[float] = None        # FCF / Net Income ratio (>1.0 = high quality earnings)
    score: Optional[float] = Field(None, description="Score 0-100")

class TechnicalAnalysis(BaseModel):
    """Technical analysis metrics"""
    sma_50: Optional[float] = None  # 50-day simple moving average
    sma_200: Optional[float] = None  # 200-day simple moving average
    ema_20: Optional[float] = None   # 20-day exponential moving average
    rsi: Optional[float] = None  # Relative Strength Index (0-100)
    macd: Optional[Dict[str, float]] = None  # MACD values
    bollinger_bands: Optional[Dict[str, float]] = None  # Upper, middle, lower
    support_level: Optional[float] = None
    resistance_level: Optional[float] = None
    trend: Optional[str] = None  # uptrend, downtrend, sideways
    atr: Optional[float] = None                         # Average True Range (14-day)
    atr_pct: Optional[float] = None                     # ATR as % of current price
    volume_ratio: Optional[float] = None                # Current volume / 20-day avg volume
    price_change_1m: Optional[float] = None             # 1-month price change %
    price_change_3m: Optional[float] = None             # 3-month price change %
    price_change_6m: Optional[float] = None             # 6-month price change %
    high_52w: Optional[float] = None                    # 52-week high
    low_52w: Optional[float] = None                     # 52-week low
    price_pct_from_52w_high: Optional[float] = None     # % below 52-week high (negative = below)
    price_pct_from_52w_low: Optional[float] = None      # % above 52-week low
    relative_strength_vs_spy: Optional[float] = None    # 3-month return vs SPY (positive = outperforming market)
    relative_strength_vs_sector: Optional[float] = None # 3-month return vs sector ETF (positive = sector leader)
    sector_etf: Optional[str] = None                    # Sector ETF used for relative strength comparison
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
    analyst_target_price: Optional[float] = Field(None, description="Analyst consensus 12-month price target")
    analyst_target_upside_pct: Optional[float] = Field(None, description="% upside to analyst consensus target")
    conviction_score: Optional[float] = Field(None, description="0-1 score: how aligned are fundamental+technical+RS signals")

class ScreeningFilter(BaseModel):
    """Stock screening filters"""
    min_pe_ratio: Optional[float] = None
    max_pe_ratio: Optional[float] = None
    max_forward_pe: Optional[float] = None         # Max forward P/E (e.g. 20)
    min_dividend_yield: Optional[float] = None
    max_debt_to_equity: Optional[float] = None
    min_revenue_growth: Optional[float] = None
    min_market_cap: Optional[float] = None
    min_volume: Optional[float] = None
    trend: Optional[str] = None  # uptrend, downtrend
    min_overall_score: float = 60
    # New enhanced filters
    min_roe: Optional[float] = None                # Min return on equity (e.g. 0.10 = 10%)
    min_roa: Optional[float] = None                # Min return on assets (e.g. 0.05 = 5%)
    min_profit_margin: Optional[float] = None      # Min net profit margin (e.g. 0.05 = 5%)
    min_operating_margin: Optional[float] = None   # Min operating margin
    max_peg_ratio: Optional[float] = None          # Max PEG ratio (e.g. 1.5 = fair value)
    max_pb_ratio: Optional[float] = None           # Max price-to-book ratio
    max_price_to_sales: Optional[float] = None     # Max price-to-sales ratio
    max_ev_ebitda: Optional[float] = None          # Max EV/EBITDA
    min_current_ratio: Optional[float] = None      # Min current ratio (e.g. 1.0)
    min_quick_ratio: Optional[float] = None        # Min quick ratio (e.g. 0.8)
    max_beta: Optional[float] = None               # Max beta (e.g. 1.5)
    min_beta: Optional[float] = None               # Min beta (for volatile plays)
    min_price_change_3m: Optional[float] = None    # Min 3-month price change (e.g. 0.05 = 5%)
    min_price_change_1m: Optional[float] = None    # Min 1-month price change
    min_volume_ratio: Optional[float] = None       # Min volume ratio vs 20-day avg (e.g. 1.2)
    min_eps: Optional[float] = None                # Min EPS (e.g. 0 to exclude loss-makers)
    min_fcf_yield: Optional[float] = None          # Min free cash flow yield (e.g. 0.02 = 2%)

class ScreeningResult(BaseModel):
    """Stock screening results"""
    total_candidates: int
    filtered_count: int
    top_picks: List[StockAnalysis]
    screening_timestamp: datetime
    deterministic_mode: bool = Field(False, description="True when a seed was supplied for stable ordering")
    seed: Optional[int] = Field(None, description="Seed value used for deterministic ordering")
