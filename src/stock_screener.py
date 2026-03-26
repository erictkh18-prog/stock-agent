"""Stock screener module for identifying suitable stocks"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import yfinance as yf
import logging
from typing import List, Optional
from datetime import datetime, timedelta
import threading
from src.config import config
from src.models import (
    StockAnalysis, ScreeningFilter, ScreeningResult,
    FundamentalAnalysis, TechnicalAnalysis, SentimentAnalysis
)
from src.fundamental_analysis import FundamentalAnalyzer
from src.technical_analysis import TechnicalAnalyzer
from src.sentiment_analysis import SentimentAnalyzer

logger = logging.getLogger(__name__)

# Sentiment score thresholds used in explanation building
_NEWS_SENTIMENT_POSITIVE_THRESHOLD = 0.2
_NEWS_SENTIMENT_NEGATIVE_THRESHOLD = -0.2

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
        self._cache_lock = threading.Lock()
        self._info_backoff_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self._cache_hits = 0
        self._cache_misses = 0
        self._analysis_requests = 0
    
    def analyze_stock(self, symbol: str) -> Optional[StockAnalysis]:
        """
        Perform complete analysis on a stock
        
        Args:
            symbol: Stock ticker symbol
        
        Returns:
            StockAnalysis object with all metrics
        """
        try:
            with self._stats_lock:
                self._analysis_requests += 1

            cached_analysis = self._get_cached_analysis(symbol)
            if cached_analysis:
                with self._stats_lock:
                    self._cache_hits += 1
                return cached_analysis

            with self._stats_lock:
                self._cache_misses += 1

            # Fetch basic info
            stock = yf.Ticker(symbol)
            info = self._safe_get_info(stock, symbol)

            current_price = self._get_current_price(stock, info, symbol)
            name = info.get('longName') or info.get('shortName') or symbol
            
            if not current_price:
                self.logger.warning(f"Could not fetch price for {symbol}")
                return None
            
            # Run all three analyses concurrently — each makes independent I/O calls
            with ThreadPoolExecutor(max_workers=3) as analysis_executor:
                future_fundamental = analysis_executor.submit(
                    self.fundamental_analyzer.analyze, symbol, stock=stock, info=info
                )
                future_technical = analysis_executor.submit(
                    self.technical_analyzer.analyze, symbol
                )
                future_sentiment = analysis_executor.submit(
                    self.sentiment_analyzer.analyze, symbol
                )
                fundamental = future_fundamental.result()
                technical = future_technical.result()
                sentiment_dict = future_sentiment.result()
            
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
                fundamental, technical, sentiment_dict
            )

            # Build plain-language explanation
            reason, contributing_factors, risk_factors = self._build_explanation(
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
                confidence=confidence,
                reason=reason,
                top_contributing_factors=contributing_factors,
                top_risk_factors=risk_factors,
            )

            with self._cache_lock:
                self.analysis_cache[symbol] = analysis
            return analysis
        
        except Exception as e:
            self.logger.error(f"Error analyzing {symbol}: {e}")
            return None

    def _get_cached_analysis(self, symbol: str) -> Optional[StockAnalysis]:
        """Return a recently cached analysis to reduce provider rate-limit pressure."""
        with self._cache_lock:
            cached = self.analysis_cache.get(symbol)
        if not cached:
            return None

        age_seconds = (datetime.now() - cached.timestamp).total_seconds()
        if age_seconds <= self.cache_ttl_seconds:
            return cached

        with self._cache_lock:
            self.analysis_cache.pop(symbol, None)
        return None

    def _safe_get_info(self, stock: yf.Ticker, symbol: str) -> dict:
        """Fetch quote info but degrade gracefully when Yahoo blocks the request."""
        with self._info_backoff_lock:
            if self.info_backoff_until and datetime.now() < self.info_backoff_until:
                return {}

        try:
            info = stock.info
            return info if isinstance(info, dict) else {}
        except Exception as exc:
            if "Too Many Requests" in str(exc):
                with self._info_backoff_lock:
                    self.info_backoff_until = datetime.now() + timedelta(minutes=5)
            self.logger.warning(f"Falling back from info lookup for {symbol}: {exc}")
            return {}

    def _analyze_symbol_for_screen(self, symbol: str, filters: ScreeningFilter) -> Optional[StockAnalysis]:
        """Analyze one symbol and return it only if it passes filters."""
        try:
            analysis = self.analyze_stock(symbol)
            if analysis and self._passes_filters(analysis, filters):
                return analysis
            return None
        except Exception as e:
            self.logger.error(f"Error screening {symbol}: {e}")
            return None

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
        top_n: int = 10, seed: Optional[int] = None
    ) -> ScreeningResult:
        """
        Screen multiple stocks and return top picks.

        Args:
            symbols: List of stock symbols to analyze
            filters: Screening filters to apply
            top_n: Number of top picks to return
            seed: When provided, enables deterministic mode.  Input symbols are
                  sorted alphabetically before processing and results are ranked
                  with a stable secondary key (symbol name) so that identical
                  inputs always produce the same ordering.

        Returns:
            ScreeningResult with filtered stocks
        """
        if filters is None:
            filters = ScreeningFilter()

        deterministic_mode = seed is not None

        # In deterministic mode sort the candidate list so that the slice taken
        # by max_symbols (applied upstream) and the parallel work queue are
        # always consistent across runs.
        work_symbols = sorted(symbols) if deterministic_mode else symbols

        results = []

        max_workers = min(20, max(1, len(work_symbols)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(self._analyze_symbol_for_screen, symbol, filters)
                for symbol in work_symbols
            ]
            for future in as_completed(futures):
                analysis = future.result()
                if analysis:
                    results.append(analysis)

        # Sort by overall score descending.  A secondary key on the symbol name
        # ensures a fully deterministic, stable ordering whenever scores tie.
        results.sort(key=lambda x: (-x.overall_score, x.symbol))

        # Get top picks
        top_picks = results[:top_n]

        return ScreeningResult(
            total_candidates=len(work_symbols),
            filtered_count=len(results),
            top_picks=top_picks,
            screening_timestamp=datetime.now(),
            deterministic_mode=deterministic_mode,
            seed=seed,
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
        Calculate overall recommendation based on all analyses.

        Weights: fundamental 40 %, technical 40 %, sentiment 20 %.
        Sentiment from free RSS feeds is noisy, so it receives a lower weight
        while fundamentals and technicals share equal importance.

        Returns:
            Tuple of (overall_score, recommendation, confidence)
        """
        scores = []
        weights = []

        # Fundamental score (40% weight)
        if fundamental and fundamental.score:
            scores.append(fundamental.score)
            weights.append(0.40)

        # Technical score (40% weight)
        if technical and technical.score:
            scores.append(technical.score)
            weights.append(0.40)

        # Sentiment score (20% weight)
        if sentiment and sentiment.get('score'):
            scores.append(sentiment['score'])
            weights.append(0.20)

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

    def _build_explanation(
        self,
        fundamental: Optional[FundamentalAnalysis],
        technical: Optional[TechnicalAnalysis],
        sentiment: Optional[SentimentAnalysis],
    ) -> tuple:
        """
        Build a plain-language explanation for the stock ranking.

        Returns:
            Tuple of (reason: str, contributing_factors: List[str], risk_factors: List[str])
        """
        contributing: list = []
        risks: list = []

        # --- Fundamental factors ---
        if fundamental:
            pe = fundamental.pe_ratio
            eps = fundamental.eps
            revenue_growth = fundamental.revenue_growth
            roe = fundamental.roe
            debt_to_equity = fundamental.debt_to_equity
            current_ratio = fundamental.current_ratio

            if eps is not None:
                if eps > 0:
                    contributing.append("positive earnings")
                else:
                    risks.append("negative earnings")

            if pe is not None:
                if 10 <= pe <= 25:
                    contributing.append("reasonable valuation")
                elif pe < 10:
                    contributing.append("low valuation")
                elif pe > 35:
                    risks.append("elevated valuation")

            if revenue_growth is not None:
                if revenue_growth > 0.15:
                    contributing.append("strong revenue growth")
                elif revenue_growth > 0.05:
                    contributing.append("steady revenue growth")
                elif revenue_growth < 0:
                    risks.append("declining revenue")

            if roe is not None and roe > 0.15:
                contributing.append("strong return on equity")

            if debt_to_equity is not None:
                if debt_to_equity < 0.5:
                    contributing.append("low debt")
                elif debt_to_equity > 2:
                    risks.append("high debt load")

            if current_ratio is not None and current_ratio < 1.0:
                risks.append("tight liquidity")

        # --- Technical factors ---
        if technical:
            trend = technical.trend
            rsi = technical.rsi
            macd = technical.macd

            if trend == "uptrend":
                contributing.append("positive price momentum")
            elif trend == "downtrend":
                risks.append("price in downtrend")

            if rsi is not None:
                if rsi > 70:
                    risks.append("overbought conditions")
                elif rsi < 30:
                    contributing.append("oversold — potential reversal")

            if macd is not None:
                histogram = macd.get("histogram", 0) or 0
                if histogram > 0:
                    contributing.append("bullish MACD signal")
                elif histogram < 0:
                    risks.append("bearish MACD signal")

        # --- Sentiment factors ---
        if sentiment:
            analyst = sentiment.analyst_sentiment
            news_val = sentiment.news_sentiment

            if analyst == "bullish" or (news_val is not None and news_val > _NEWS_SENTIMENT_POSITIVE_THRESHOLD):
                contributing.append("positive news sentiment")
            elif analyst == "bearish" or (news_val is not None and news_val < _NEWS_SENTIMENT_NEGATIVE_THRESHOLD):
                risks.append("negative news sentiment")

        # --- Build reason string ---
        top_contributing = contributing[:3]
        top_risks = risks[:3]

        if top_contributing and top_risks:
            reason = (
                f"{', '.join(top_contributing).capitalize()}; "
                f"watch for {top_risks[0]}"
            )
        elif top_contributing:
            reason = ', '.join(top_contributing).capitalize()
        elif top_risks:
            reason = f"Notable risks: {', '.join(top_risks)}"
        else:
            reason = "Insufficient data for detailed analysis"

        return reason, top_contributing, top_risks

    def get_runtime_stats(self) -> dict:
        """Return runtime metrics useful for monitoring and tuning."""
        with self._stats_lock:
            cache_hits = self._cache_hits
            cache_misses = self._cache_misses
            analysis_requests = self._analysis_requests

        total_cache_lookups = cache_hits + cache_misses
        cache_hit_rate = (
            round((cache_hits / total_cache_lookups) * 100, 2)
            if total_cache_lookups
            else 0.0
        )

        with self._cache_lock:
            cache_size = len(self.analysis_cache)

        with self._info_backoff_lock:
            info_backoff_active = bool(
                self.info_backoff_until and datetime.now() < self.info_backoff_until
            )

        return {
            "analysis_requests": analysis_requests,
            "cache_hits": cache_hits,
            "cache_misses": cache_misses,
            "cache_hit_rate_pct": cache_hit_rate,
            "cache_size": cache_size,
            "cache_ttl_seconds": self.cache_ttl_seconds,
            "info_backoff_active": info_backoff_active,
        }
