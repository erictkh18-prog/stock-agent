"""Fundamental analysis module for stocks"""
import yfinance as yf
import logging
from typing import Optional
from datetime import datetime, timedelta
from src.models import FundamentalAnalysis

logger = logging.getLogger(__name__)

class FundamentalAnalyzer:
    """Analyzes fundamental metrics of stocks"""
    
    def __init__(self):
        self.logger = logger
    
    def analyze(self, symbol: str) -> Optional[FundamentalAnalysis]:
        """
        Analyze fundamental metrics for a stock
        
        Args:
            symbol: Stock ticker symbol (e.g., 'AAPL')
        
        Returns:
            FundamentalAnalysis object with metrics
        """
        try:
            # Fetch stock data
            stock = yf.Ticker(symbol)
            info = stock.info
            
            # Extract fundamental metrics
            pe_ratio = info.get('trailingPE')
            eps = info.get('trailingEps')
            dividend_yield = info.get('dividendYield')
            debt_to_equity = info.get('debtToEquity')
            current_ratio = info.get('currentRatio')
            roa = info.get('returnOnAssets')
            roe = info.get('returnOnEquity')
            peg_ratio = info.get('pegRatio')
            
            # Calculate growth metrics
            revenue_growth = self._calculate_revenue_growth(stock)
            profit_margin = info.get('profitMargins')
            
            # Calculate score
            score = self._calculate_fundamental_score(
                pe_ratio, eps, dividend_yield, debt_to_equity,
                current_ratio, roa, roe, revenue_growth
            )
            
            return FundamentalAnalysis(
                pe_ratio=pe_ratio,
                eps=eps,
                dividend_yield=dividend_yield,
                debt_to_equity=debt_to_equity,
                current_ratio=current_ratio,
                roa=roa,
                roe=roe,
                revenue_growth=revenue_growth,
                profit_margin=profit_margin,
                peg_ratio=peg_ratio,
                score=score
            )
        
        except Exception as e:
            self.logger.error(f"Error analyzing fundamentals for {symbol}: {e}")
            return None
    
    def _calculate_revenue_growth(self, stock) -> Optional[float]:
        """Calculate year-over-year revenue growth"""
        try:
            quarterly_financials = stock.quarterly_financials
            if quarterly_financials is None or quarterly_financials.empty:
                return None
            
            # Get revenue data
            if 'Total Revenue' in quarterly_financials.index:
                revenues = quarterly_financials.loc['Total Revenue']
                # Get last 4 quarters vs previous 4 quarters
                if len(revenues) >= 8:
                    recent = revenues.iloc[:4].mean()
                    previous = revenues.iloc[4:8].mean()
                    if previous > 0:
                        return (recent - previous) / previous
            return None
        except Exception:
            return None
    
    def _calculate_fundamental_score(
        self, pe_ratio, eps, dividend_yield, debt_to_equity,
        current_ratio, roa, roe, revenue_growth
    ) -> float:
        """
        Calculate a fundamental score (0-100)
        
        Scoring logic:
        - Low P/E ratio (under 15): positive
        - Positive EPS: positive
        - High dividend yield: positive
        - Low debt to equity: positive
        - Strong current ratio (> 1.5): positive
        - Positive ROA and ROE: positive
        - Positive revenue growth: positive
        """
        score = 50  # Start with neutral score
        
        # P/E Ratio analysis (ideal: 15-25)
        if pe_ratio:
            if 10 <= pe_ratio <= 25:
                score += 10
            elif pe_ratio < 10:
                score += 8
            elif pe_ratio > 30:
                score -= 5
        
        # EPS analysis
        if eps and eps > 0:
            score += 10
        elif eps and eps < 0:
            score -= 10
        
        # Dividend yield
        if dividend_yield and dividend_yield > 0.02:
            score += 5
        
        # Debt to equity (lower is better)
        if debt_to_equity:
            if debt_to_equity < 0.5:
                score += 10
            elif debt_to_equity < 1:
                score += 5
            elif debt_to_equity > 2:
                score -= 5
        
        # Current ratio (should be > 1.5)
        if current_ratio:
            if current_ratio > 1.5:
                score += 5
            elif current_ratio < 1:
                score -= 10
        
        # ROA (return on assets)
        if roa and roa > 0.05:
            score += 5
        
        # ROE (return on equity)
        if roe and roe > 0.15:
            score += 5
        
        # Revenue growth
        if revenue_growth and revenue_growth > 0.1:
            score += 10
        elif revenue_growth and revenue_growth < 0:
            score -= 5
        
        # Clamp score between 0 and 100
        return max(0, min(100, score))
