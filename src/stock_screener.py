"""Stock screener module for identifying suitable stocks"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import yfinance as yf
import requests
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
from src.macro_regime import get_macro_regime
from src.insider_activity import get_insider_signal

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
    
    def analyze_stock(self, symbol: str, fast_mode: bool = False) -> Optional[StockAnalysis]:
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

            # Stage 2/3: sector and analyst target from info dict
            sector_for_rs = (info.get('sector') or '').lower().strip()
            analyst_target_price: Optional[float] = info.get('targetMeanPrice') if info else None
            analyst_target_upside_pct: Optional[float] = None
            if analyst_target_price and current_price and current_price > 0:
                analyst_target_upside_pct = round(
                    (analyst_target_price - current_price) / current_price * 100.0, 2
                )

            # In fast_mode, skip live sentiment feed calls to reduce broad-scan latency.
            analysis_workers = 2 if fast_mode else 3
            with ThreadPoolExecutor(max_workers=analysis_workers) as analysis_executor:
                future_fundamental = analysis_executor.submit(
                    self.fundamental_analyzer.analyze,
                    symbol,
                    stock=stock,
                    info=info,
                    enable_web_fallback=not fast_mode,
                )
                future_technical = analysis_executor.submit(
                    self.technical_analyzer.analyze, symbol, sector=sector_for_rs
                )
                future_sentiment = None
                if not fast_mode:
                    future_sentiment = analysis_executor.submit(
                        self.sentiment_analyzer.analyze, symbol
                    )
                fundamental = future_fundamental.result()
                technical = future_technical.result()
                if future_sentiment is not None:
                    sentiment_dict = future_sentiment.result()
                else:
                    sentiment_dict = {
                        "news_sentiment": 0.0,
                        "news_count": 0,
                        "analyst_sentiment": "neutral",
                        "institutional_ownership": None,
                        "score": 50.0,
                    }
            
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

            # Build plain-language explanation
            reason, contributing_factors, risk_factors = self._build_explanation(
                fundamental, technical, sentiment
            )

            # Item 3: Macro regime (cached 1 hour — free)
            # In fast_mode we still fetch macro because it is already cached.
            macro_data = get_macro_regime()
            macro_regime = macro_data.get("regime")          # "bull" | "bear" | "neutral"
            macro_regime_score = macro_data.get("regime_score")

            # Item 3: Insider activity — SEC Form 4 (15-day lookback)
            # Skip in fast_mode to avoid SEC rate-limit delays during broad scans.
            insider_data = {"signal": "unknown", "buy_count": 0, "sell_count": 0, "net_transactions": 0}
            if not fast_mode:
                try:
                    insider_data = get_insider_signal(symbol, lookback_days=15)
                except Exception as _ie:
                    self.logger.debug("Insider signal fetch failed for %s: %s", symbol, _ie)

            insider_signal = insider_data.get("signal", "unknown")
            insider_buy_count = insider_data.get("buy_count", 0)
            insider_sell_count = insider_data.get("sell_count", 0)

            # Extended conviction score — now 8 independent factors
            conviction_factors = 0
            if fundamental and fundamental.score is not None and fundamental.score > 60:
                conviction_factors += 1
            if technical and technical.score is not None and technical.score > 60:
                conviction_factors += 1
            if technical and getattr(technical, 'relative_strength_vs_spy', None) is not None:
                if technical.relative_strength_vs_spy > 0:
                    conviction_factors += 1
            if fundamental and getattr(fundamental, 'roic', None) is not None:
                if fundamental.roic > 0.12:
                    conviction_factors += 1
            if fundamental and getattr(fundamental, 'eps_acceleration', None) is not None:
                if fundamental.eps_acceleration > 0:
                    conviction_factors += 1
            if fundamental and getattr(fundamental, 'eps_forward_revision', None) is not None:
                if fundamental.eps_forward_revision > 0:
                    conviction_factors += 1
            if insider_signal == "buying":
                conviction_factors += 1
            if macro_regime != "bear":
                conviction_factors += 1
            conviction_score_val = round(conviction_factors / 8.0, 2)

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
                analyst_target_price=analyst_target_price,
                analyst_target_upside_pct=analyst_target_upside_pct,
                conviction_score=conviction_score_val,
                macro_regime=macro_regime,
                macro_regime_score=macro_regime_score,
                insider_signal=insider_signal,
                insider_buy_count=insider_buy_count,
                insider_sell_count=insider_sell_count,
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

    def _analyze_symbol_for_screen(
        self,
        symbol: str,
        filters: ScreeningFilter,
        fast_mode: bool = False,
    ) -> Optional[StockAnalysis]:
        """Analyze one symbol and return it only if it passes filters."""
        try:
            analysis = self.analyze_stock(symbol, fast_mode=fast_mode)
            if analysis and self._passes_filters(analysis, filters):
                return analysis
            return None
        except Exception as e:
            self.logger.error(f"Error screening {symbol}: {e}")
            return None

    def _fetch_price_direct(self, symbol: str) -> Optional[float]:
        """Fetch current price directly from Yahoo Finance v7 quote API using plain HTTP.

        This bypasses yfinance's cookie/crumb authentication flow and works even
        when yfinance's internal auth fails (e.g. due to rate limiting or IP blocks).
        """
        try:
            resp = requests.get(
                "https://query1.finance.yahoo.com/v7/finance/quote",
                params={"symbols": symbol},
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    )
                },
                timeout=10,
            )
            resp.raise_for_status()
            result = resp.json().get("quoteResponse", {}).get("result", [])
            if result:
                for key in ("regularMarketPrice", "ask", "bid", "previousClose"):
                    val = result[0].get(key)
                    if val is not None:
                        return float(val)
        except Exception as exc:
            self.logger.debug(f"Direct price fetch failed for {symbol}: {exc}")
        return None

    def _get_current_price(self, stock: yf.Ticker, info: dict, symbol: str) -> Optional[float]:
        """Resolve the best available current price using multiple Yahoo data paths."""
        # 1. Try values already in the info dict (no extra network call).
        for key in ("currentPrice", "regularMarketPrice", "previousClose"):
            val = info.get(key)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    continue

        # 2. Try fast_info snake_case properties (yfinance 1.x API).
        try:
            fast_info = stock.fast_info
            for attr in ("last_price", "previous_close"):
                try:
                    val = getattr(fast_info, attr, None)
                    if val is not None:
                        return float(val)
                except Exception:
                    continue
        except Exception as exc:
            self.logger.debug(f"fast_info lookup failed for {symbol}: {exc}")

        # 3. Direct HTTP fallback — bypasses yfinance auth flow entirely.
        direct_price = self._fetch_price_direct(symbol)
        if direct_price is not None:
            return direct_price

        # 4. History fallback using a fresh Ticker so that a previously-failed
        #    info fetch (which sets _already_fetched=True internally) does not
        #    poison the timezone lookup inside stock.history().
        try:
            fresh = yf.Ticker(symbol)
            history = fresh.history(period="5d", interval="1d", auto_adjust=False)
            if history is not None and not history.empty:
                close_series = history["Close"].dropna()
                if not close_series.empty:
                    return float(close_series.iloc[-1])
        except Exception as exc:
            self.logger.warning(f"history fallback failed for {symbol}: {exc}")

        return None
    
    def screen_stocks(
        self, symbols: List[str], filters: Optional[ScreeningFilter] = None,
        top_n: int = 10, seed: Optional[int] = None, fast_mode: bool = False
    ) -> ScreeningResult:
        """
        Screen multiple stocks and return top picks.

        Args:
            symbols: List of stock symbols to analyze
            filters: Screening filters to apply
            top_n: Number of top picks to return
            seed: When provided, enables deterministic mode. Input symbols are
                sorted alphabetically before processing and tied scores are
                broken with a stable seed-derived key so identical inputs plus
                the same seed produce the same ordering.
            fast_mode: When True, disable expensive web-fundamental fallback
                     calls to improve broad-market scan latency.

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
            future_to_symbol = {
                executor.submit(self._analyze_symbol_for_screen, symbol, filters, fast_mode): symbol
                for symbol in work_symbols
            }
            for future in as_completed(future_to_symbol):
                symbol = future_to_symbol[future]
                try:
                    analysis = future.result()
                    if analysis:
                        results.append(analysis)
                except Exception as exc:
                    self.logger.warning("Failed to fetch %s: %s; skipping", symbol, exc)

        # Sort by overall score descending. In deterministic mode, ties are
        # broken with a stable seed-derived key so the supplied seed has a real
        # effect without changing the primary score ordering.
        results.sort(key=lambda x: self._rank_sort_key(x, seed))
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
            fund = analysis.fundamental

            if filters.min_pe_ratio and fund.pe_ratio:
                if fund.pe_ratio < filters.min_pe_ratio:
                    return False
            
            if filters.max_pe_ratio and fund.pe_ratio:
                if fund.pe_ratio > filters.max_pe_ratio:
                    return False

            if filters.max_forward_pe and fund.forward_pe:
                if fund.forward_pe > filters.max_forward_pe:
                    return False
            
            if filters.min_dividend_yield and fund.dividend_yield:
                if fund.dividend_yield < filters.min_dividend_yield:
                    return False
            
            if filters.max_debt_to_equity and fund.debt_to_equity:
                if fund.debt_to_equity > filters.max_debt_to_equity:
                    return False
            
            if filters.min_revenue_growth and fund.revenue_growth:
                if fund.revenue_growth < filters.min_revenue_growth:
                    return False

            if filters.min_roe is not None and fund.roe is not None:
                if fund.roe < filters.min_roe:
                    return False

            if filters.min_roa is not None and fund.roa is not None:
                if fund.roa < filters.min_roa:
                    return False

            if filters.min_profit_margin is not None and fund.profit_margin is not None:
                if fund.profit_margin < filters.min_profit_margin:
                    return False

            if filters.min_operating_margin is not None and fund.operating_margin is not None:
                if fund.operating_margin < filters.min_operating_margin:
                    return False

            if filters.max_peg_ratio and fund.peg_ratio:
                if fund.peg_ratio > filters.max_peg_ratio:
                    return False

            if filters.max_pb_ratio and fund.pb_ratio:
                if fund.pb_ratio > filters.max_pb_ratio:
                    return False

            if filters.max_price_to_sales and fund.price_to_sales:
                if fund.price_to_sales > filters.max_price_to_sales:
                    return False

            if filters.max_ev_ebitda and fund.ev_ebitda:
                if fund.ev_ebitda > filters.max_ev_ebitda:
                    return False

            if filters.min_current_ratio is not None and fund.current_ratio is not None:
                if fund.current_ratio < filters.min_current_ratio:
                    return False

            if filters.min_quick_ratio is not None and fund.quick_ratio is not None:
                if fund.quick_ratio < filters.min_quick_ratio:
                    return False

            if filters.min_eps is not None and fund.eps is not None:
                if fund.eps < filters.min_eps:
                    return False

            if filters.min_fcf_yield is not None and fund.fcf_yield is not None:
                if fund.fcf_yield < filters.min_fcf_yield:
                    return False

            if filters.max_beta is not None and fund.beta is not None:
                if fund.beta > filters.max_beta:
                    return False

            if filters.min_beta is not None and fund.beta is not None:
                if fund.beta < filters.min_beta:
                    return False
        
        # Technical filters
        if analysis.technical:
            tech = analysis.technical

            if filters.trend and tech.trend != filters.trend:
                return False

            if filters.min_price_change_3m is not None and tech.price_change_3m is not None:
                if tech.price_change_3m < filters.min_price_change_3m:
                    return False

            if filters.min_price_change_1m is not None and tech.price_change_1m is not None:
                if tech.price_change_1m < filters.min_price_change_1m:
                    return False

            if filters.min_volume_ratio is not None and tech.volume_ratio is not None:
                if tech.volume_ratio < filters.min_volume_ratio:
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

        Weights: fundamental 40%, technical 40%, sentiment 20%.
        Sentiment from free RSS feeds is noisy, so it receives a lower weight
        while fundamentals and technicals share equal importance.

        Returns:
            Tuple of (overall_score, recommendation, confidence)
        """
        scores = []
        weights = []

        # Fundamental score (40% weight)
        if fundamental and fundamental.score is not None:
            scores.append(fundamental.score)
            weights.append(0.40)

        # Technical score (40% weight)
        if technical and technical.score is not None:
            scores.append(technical.score)
            weights.append(0.40)

        # Sentiment score (20% weight)
        if sentiment and sentiment.score is not None:
            scores.append(sentiment.score)
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

    def _rank_sort_key(self, analysis: StockAnalysis, seed: Optional[int]) -> tuple:
        """Return the ranking sort key, using the seed only for stable tie-breaking."""
        if seed is None:
            return (-analysis.overall_score, analysis.symbol)
        return (-analysis.overall_score, self._seeded_tie_breaker(analysis.symbol, seed), analysis.symbol)

    def _seeded_tie_breaker(self, symbol: str, seed: int) -> int:
        """Build a stable integer used to break ties reproducibly for a given seed."""
        digest = hashlib.sha256(f"{seed}:{symbol}".encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big")

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
            forward_pe = fundamental.forward_pe
            eps = fundamental.eps
            revenue_growth = fundamental.revenue_growth
            roe = fundamental.roe
            roa = fundamental.roa
            debt_to_equity = fundamental.debt_to_equity
            current_ratio = fundamental.current_ratio
            quick_ratio = fundamental.quick_ratio
            profit_margin = fundamental.profit_margin
            operating_margin = fundamental.operating_margin
            peg_ratio = fundamental.peg_ratio
            pb_ratio = fundamental.pb_ratio
            fcf_yield = fundamental.fcf_yield
            beta = fundamental.beta

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

            if forward_pe is not None and forward_pe > 0:
                if forward_pe <= 15:
                    contributing.append("attractive forward valuation")
                elif forward_pe > 30:
                    risks.append("high forward P/E")

            if peg_ratio is not None and peg_ratio > 0:
                if peg_ratio < 1.0:
                    contributing.append("undervalued relative to growth (PEG < 1)")
                elif peg_ratio > 2.0:
                    risks.append("expensive relative to growth (PEG > 2)")

            if pb_ratio is not None and pb_ratio > 0:
                if pb_ratio < 1.5:
                    contributing.append("trading near book value")
                elif pb_ratio > 5.0:
                    risks.append("high price-to-book ratio")

            if revenue_growth is not None:
                if revenue_growth > 0.15:
                    contributing.append("strong revenue growth")
                elif revenue_growth > 0.05:
                    contributing.append("steady revenue growth")
                elif revenue_growth < 0:
                    risks.append("declining revenue")

            if roe is not None and roe > 0.15:
                contributing.append("strong return on equity")
            if roe is not None and roe < 0:
                risks.append("negative return on equity")

            if roa is not None and roa > 0.08:
                contributing.append("efficient asset utilisation")

            # Stage 1: Advanced quality metrics
            roic = getattr(fundamental, 'roic', None)
            if roic is not None:
                if roic > 0.20:
                    contributing.append("excellent capital allocation (high ROIC)")
                elif roic > 0.12:
                    contributing.append("strong return on invested capital")
                elif roic < 0:
                    risks.append("negative return on invested capital")

            eps_acceleration = getattr(fundamental, 'eps_acceleration', None)
            if eps_acceleration is not None:
                if eps_acceleration > 0.05:
                    contributing.append("accelerating earnings growth")
                elif eps_acceleration < -0.05:
                    risks.append("decelerating earnings momentum")

            fcf_conversion = getattr(fundamental, 'fcf_conversion', None)
            if fcf_conversion is not None:
                if fcf_conversion > 1.0:
                    contributing.append("high-quality earnings (strong FCF conversion)")
                elif fcf_conversion < 0:
                    risks.append("poor earnings quality (negative FCF vs net income)")

            # Item 2: Forward EPS revision signal
            eps_fwd_rev = getattr(fundamental, 'eps_forward_revision', None)
            if eps_fwd_rev is not None:
                if eps_fwd_rev > 0.15:
                    contributing.append("analysts sharply raising earnings estimates")
                elif eps_fwd_rev > 0:
                    contributing.append("positive analyst earnings revision")
                elif eps_fwd_rev < -0.15:
                    risks.append("analysts cutting earnings estimates significantly")
                elif eps_fwd_rev < 0:
                    risks.append("downward analyst earnings revision")

            # Item 6: Short interest
            short_float = getattr(fundamental, 'short_float_pct', None)
            if short_float is not None:
                if short_float > 0.25:
                    risks.append("heavily shorted stock (>25% of float)")
                elif short_float > 0.15:
                    risks.append("elevated short interest")

            if profit_margin is not None:
                if profit_margin > 0.15:
                    contributing.append("strong profit margins")
                elif profit_margin < 0:
                    risks.append("loss-making operations")

            if operating_margin is not None and operating_margin < 0:
                risks.append("negative operating margin")

            if debt_to_equity is not None:
                if debt_to_equity < 0.5:
                    contributing.append("low debt")
                elif debt_to_equity > 2:
                    risks.append("high debt load")

            if current_ratio is not None and current_ratio < 1.0:
                risks.append("tight liquidity")

            if quick_ratio is not None and quick_ratio < 0.7:
                risks.append("weak quick ratio")

            if fcf_yield is not None:
                if fcf_yield > 0.04:
                    contributing.append("strong free cash flow yield")
                elif fcf_yield < 0:
                    risks.append("negative free cash flow")

            if beta is not None and beta > 2.0:
                risks.append("high market volatility (beta > 2)")

        # --- Technical factors ---
        if technical:
            trend = technical.trend
            rsi = technical.rsi
            macd = technical.macd
            price_change_3m = technical.price_change_3m
            volume_ratio = technical.volume_ratio
            price_pct_from_52w_high = technical.price_pct_from_52w_high

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

            if price_change_3m is not None:
                if price_change_3m > 0.15:
                    contributing.append("strong 3-month momentum")
                elif price_change_3m < -0.15:
                    risks.append("weak 3-month price performance")

            if volume_ratio is not None and volume_ratio >= 1.5:
                contributing.append("above-average volume confirmation")

            if price_pct_from_52w_high is not None:
                if price_pct_from_52w_high >= -0.05:
                    contributing.append("near 52-week high — breakout zone")
                elif price_pct_from_52w_high < -0.40:
                    risks.append("deep below 52-week high")

            # Stage 2: Relative strength signals
            rs_spy = getattr(technical, 'relative_strength_vs_spy', None)
            if rs_spy is not None:
                if rs_spy > 0.05:
                    contributing.append("outperforming the broader market")
                elif rs_spy < -0.05:
                    risks.append("underperforming the broader market")

            rs_sector = getattr(technical, 'relative_strength_vs_sector', None)
            if rs_sector is not None:
                if rs_sector > 0.05:
                    contributing.append("sector leadership — outperforming peers")
                elif rs_sector < -0.05:
                    risks.append("lagging sector peers")

            # Item 5: Breakout pattern
            if getattr(technical, 'is_breakout', None) is True:
                contributing.append("breaking out on high volume — institutional buying signal")

        # --- Macro regime (Item 4) ---
        # Surface macro risk even if specific stock factors look neutral.
        # Only show in explanation when macro is notably bad or good.
        # (macro_regime is on StockAnalysis, not on fundamental/technical sub-objects;
        #  we access it via the closure over the local variables set above.)

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
