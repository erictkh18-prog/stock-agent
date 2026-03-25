"""Stock screener module for identifying suitable stocks"""
import yfinance as yf
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional
from datetime import datetime, timedelta
from src.config import config
from src.models import (
    StockAnalysis, ScreeningFilter, ScreeningResult,
    FundamentalAnalysis, TechnicalAnalysis, SentimentAnalysis
)
from src.fundamental_analysis import FundamentalAnalyzer
from src.technical_analysis import TechnicalAnalyzer
from src.sentiment_analysis import SentimentAnalyzer

logger = logging.getLogger(__name__)

class StockScreener:
    """Screen and analyze stocks for investment"""
    
    def __init__(self):
        self.fundamental_analyzer = FundamentalAnalyzer()
        self.technical_analyzer = TechnicalAnalyzer()
        self.sentiment_analyzer = SentimentAnalyzer()
        self.logger = logger
        self.cache_ttl_seconds = config.CACHE_TTL_SECONDS
        self.analysis_cache = {}
        self.info_backoff_until = None
    
    def analyze_stock(self, symbol: str) -> Optional[StockAnalysis]:
        """
        Perform complete analysis on a stock
        
        Args:
            symbol: Stock ticker symbol
        
        Returns:
            StockAnalysis object with all metrics
        """
        try:
            cached_analysis = self._get_cached_analysis(symbol)
            if cached_analysis:
                return cached_analysis

            # Fetch basic info
            stock = yf.Ticker(symbol)
            info = self._safe_get_info(stock, symbol)

            current_price = self._get_current_price(stock, info, symbol)
            name = info.get('longName') or info.get('shortName') or symbol
            
            if not current_price:
                self.logger.warning(f"Could not fetch price for {symbol}")
                return None
            
            # Run all analyses
            fundamental = self.fundamental_analyzer.analyze(symbol, stock=stock, info=info)
            technical = self.technical_analyzer.analyze(symbol)
            sentiment_dict = self.sentiment_analyzer.analyze(symbol)
            
            # Convert sentiment dict to SentimentAnalysis model
            sentiment = SentimentAnalysis(
                news_sentiment=sentiment_dict.get('news_sentiment'),
                news_count=sentiment_dict.get('news_count'),
                analyst_sentiment=sentiment_dict.get('analyst_sentiment'),
                institutional_ownership=sentiment_dict.get('institutional_ownership'),
                score=sentiment_dict.get('score')
            )
            
            # Calculate overall score and recommendation
            overall_score, recommendation, confidence = self._calculate_recommendation(
                fundamental, technical, sentiment
            )
            
            analysis = StockAnalysis(
                symbol=symbol,
                name=name,
                current_price=current_price,
                timestamp=datetime.now(),
                fundamental=fundamental,
                technical=technical,
                sentiment=sentiment,
                overall_score=overall_score,
                recommendation=recommendation,
                confidence=confidence
            )

            self.analysis_cache[symbol] = analysis
            return analysis
        
        except Exception as e:
            self.logger.error(f"Error analyzing {symbol}: {e}")
            return None

    def _get_cached_analysis(self, symbol: str) -> Optional[StockAnalysis]:
        """Return a recently cached analysis to reduce provider rate-limit pressure."""
        cached = self.analysis_cache.get(symbol)
        if not cached:
            return None

        age_seconds = (datetime.now() - cached.timestamp).total_seconds()
        if age_seconds <= self.cache_ttl_seconds:
            return cached

        self.analysis_cache.pop(symbol, None)
        return None

    def _safe_get_info(self, stock: yf.Ticker, symbol: str) -> dict:
        """Fetch quote info but degrade gracefully when Yahoo blocks the request."""
        if self.info_backoff_until and datetime.now() < self.info_backoff_until:
            return {}

        try:
            info = stock.info
            return info if isinstance(info, dict) else {}
        except Exception as exc:
            if "Too Many Requests" in str(exc):
                self.info_backoff_until = datetime.now() + timedelta(minutes=5)
            self.logger.warning(f"Falling back from info lookup for {symbol}: {exc}")
            return {}

    def _get_current_price(self, stock: yf.Ticker, info: dict, symbol: str) -> Optional[float]:
        """Resolve the best available current price using multiple Yahoo data paths."""
        price_candidates = [
            info.get('currentPrice'),
            info.get('regularMarketPrice'),
            info.get('previousClose'),
        ]

        try:
            fast_info = stock.fast_info
            for key in ('lastPrice', 'regularMarketPrice', 'previousClose'):
                try:
                    value = fast_info.get(key)
                except Exception:
                    value = None
                price_candidates.append(value)
        except Exception as exc:
            self.logger.warning(f"fast_info lookup failed for {symbol}: {exc}")

        for price in price_candidates:
            if price is not None:
                try:
                    return float(price)
                except (TypeError, ValueError):
                    continue

        try:
            history = stock.history(period="5d", interval="1d", auto_adjust=False)
            if not history.empty:
                close_series = history['Close'].dropna()
                if not close_series.empty:
                    return float(close_series.iloc[-1])
        except Exception as exc:
            self.logger.warning(f"history fallback failed for {symbol}: {exc}")

        return None
    
    def screen_stocks(
        self, symbols: List[str], filters: Optional[ScreeningFilter] = None,
        top_n: int = 10
    ) -> ScreeningResult:
        """
        Screen multiple stocks and return top picks
        
        Args:
            symbols: List of stock symbols to analyze
            filters: Screening filters to apply
            top_n: Number of top picks to return
        
        Returns:
            ScreeningResult with filtered stocks
        """
        if filters is None:
            filters = ScreeningFilter()
        
        results = []

        # Analyze each stock concurrently to reduce wall-clock time
        # Cap at 8 workers: most watchlists are ≤15 symbols and each analysis
        # makes several network calls, so 8 threads balances throughput without
        # overwhelming the Yahoo Finance rate-limits.
        max_workers = min(8, len(symbols))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_symbol = {
                executor.submit(self.analyze_stock, symbol): symbol
                for symbol in symbols
            }
            for future in as_completed(future_to_symbol):
                symbol = future_to_symbol[future]
                try:
                    analysis = future.result()
                    if analysis and self._passes_filters(analysis, filters):
                        results.append(analysis)
                except Exception as e:
                    self.logger.error(f"Error screening {symbol}: {e}")
        
        # Sort by overall score (descending)
        results.sort(key=lambda x: x.overall_score, reverse=True)
        
        # Get top picks
        top_picks = results[:top_n]
        
        return ScreeningResult(
            total_candidates=len(symbols),
            filtered_count=len(results),
            top_picks=top_picks,
            screening_timestamp=datetime.now()
        )
    
    def _passes_filters(self, analysis: StockAnalysis, filters: ScreeningFilter) -> bool:
        """Check if stock passes screening filters"""
        
        # Overall score filter
        if analysis.overall_score < filters.min_overall_score:
            return False
        
        # Fundamental filters
        if analysis.fundamental:
            if filters.min_pe_ratio and analysis.fundamental.pe_ratio:
                if analysis.fundamental.pe_ratio < filters.min_pe_ratio:
                    return False
            
            if filters.max_pe_ratio and analysis.fundamental.pe_ratio:
                if analysis.fundamental.pe_ratio > filters.max_pe_ratio:
                    return False
            
            if filters.min_dividend_yield and analysis.fundamental.dividend_yield:
                if analysis.fundamental.dividend_yield < filters.min_dividend_yield:
                    return False
            
            if filters.max_debt_to_equity and analysis.fundamental.debt_to_equity:
                if analysis.fundamental.debt_to_equity > filters.max_debt_to_equity:
                    return False
            
            if filters.min_revenue_growth and analysis.fundamental.revenue_growth:
                if analysis.fundamental.revenue_growth < filters.min_revenue_growth:
                    return False
        
        # Technical filters
        if analysis.technical:
            if filters.trend and analysis.technical.trend != filters.trend:
                return False
        
        return True
    
    def _calculate_recommendation(
        self,
        fundamental: Optional[FundamentalAnalysis],
        technical: Optional[TechnicalAnalysis],
        sentiment: Optional[SentimentAnalysis]
    ) -> tuple:
        """
        Calculate overall recommendation based on all analyses
        
        Returns:
            Tuple of (overall_score, recommendation, confidence)
        """
        scores = []
        weights = []
        
        # Fundamental score (40% weight)
        if fundamental and fundamental.score:
            scores.append(fundamental.score)
            weights.append(0.40)
        
        # Technical score (35% weight)
        if technical and technical.score:
            scores.append(technical.score)
            weights.append(0.35)
        
        # Sentiment score (25% weight)
        if sentiment and sentiment.score:
            scores.append(sentiment.score)
            weights.append(0.25)
        
        # Calculate weighted average
        if scores:
            overall_score = sum(s * w for s, w in zip(scores, weights)) / sum(weights)
        else:
            overall_score = 50
        
        # Determine recommendation
        if overall_score >= 70:
            recommendation = "BUY"
            confidence = min(1.0, (overall_score - 70) / 30)
        elif overall_score >= 50:
            recommendation = "HOLD"
            confidence = 0.5
        else:
            recommendation = "SELL"
            confidence = min(1.0, (50 - overall_score) / 50)
        
        return overall_score, recommendation, confidence
