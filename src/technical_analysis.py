"""Technical analysis module for stocks"""
import yfinance as yf
import pandas as pd
import logging
from typing import Optional, Dict, Tuple
from datetime import datetime, timedelta
from src.models import TechnicalAnalysis

logger = logging.getLogger(__name__)

class TechnicalAnalyzer:
    """Analyzes technical indicators of stocks"""
    
    def __init__(self):
        self.logger = logger
    
    def analyze(self, symbol: str, lookback_days: int = 365, stock: Optional[yf.Ticker] = None) -> Optional[TechnicalAnalysis]:
        """
        Analyze technical indicators for a stock

        Args:
            symbol: Stock ticker symbol
            lookback_days: Number of days of historical data to analyze
            stock: Optional pre-created yf.Ticker instance. A fresh instance is
                   always used for the history fetch to avoid stale internal state
                   (e.g. a previously-failed info call that sets _already_fetched=True).

        Returns:
            TechnicalAnalysis object with indicators
        """
        try:
            # Always use a fresh Ticker for history to avoid inheriting a
            # broken _already_fetched state from a prior failed info lookup.
            fetch_stock = yf.Ticker(symbol)
            end_date = datetime.now()
            start_date = end_date - timedelta(days=lookback_days)

            hist = fetch_stock.history(start=start_date, end=end_date)
            if hist is None or hist.empty:
                self.logger.warning(f"No historical data for {symbol}")
                return None
            
            # Calculate moving averages
            sma_50 = self._calculate_sma(hist, 50)
            sma_200 = self._calculate_sma(hist, 200)
            ema_20 = self._calculate_ema(hist, 20)
            
            # Get current price
            current_price = hist['Close'].iloc[-1] if len(hist) > 0 else None
            
            # Calculate RSI
            rsi = self._calculate_rsi(hist, 14)
            
            # Calculate MACD
            macd = self._calculate_macd(hist)
            
            # Calculate Bollinger Bands
            bollinger_bands = self._calculate_bollinger_bands(hist, 20)
            
            # Find support and resistance
            support, resistance = self._find_support_resistance(hist)
            
            # Determine trend
            trend = self._determine_trend(sma_50, sma_200, current_price)

            # Average True Range (14-day volatility)
            atr = self._calculate_atr(hist, 14)
            atr_pct = (atr / current_price) if (atr is not None and current_price) else None

            # Volume ratio (today vs 20-day average)
            volume_ratio = self._calculate_volume_ratio(hist, 20)

            # Price momentum
            price_change_1m = self._calculate_price_change(hist, 21)
            price_change_3m = self._calculate_price_change(hist, 63)
            price_change_6m = self._calculate_price_change(hist, 126)

            # 52-week high/low
            high_52w, low_52w = self._calculate_52w_high_low(hist)
            price_pct_from_52w_high: Optional[float] = None
            price_pct_from_52w_low: Optional[float] = None
            if current_price and high_52w and high_52w > 0:
                price_pct_from_52w_high = (current_price - high_52w) / high_52w
            if current_price and low_52w and low_52w > 0:
                price_pct_from_52w_low = (current_price - low_52w) / low_52w
            
            # Calculate technical score
            score = self._calculate_technical_score(
                sma_50, sma_200, rsi, trend, current_price, support, resistance,
                macd, volume_ratio, price_change_1m, price_change_3m,
                price_pct_from_52w_high, atr_pct
            )
            
            return TechnicalAnalysis(
                sma_50=sma_50,
                sma_200=sma_200,
                ema_20=ema_20,
                rsi=rsi,
                macd=macd,
                bollinger_bands=bollinger_bands,
                support_level=support,
                resistance_level=resistance,
                trend=trend,
                atr=atr,
                atr_pct=atr_pct,
                volume_ratio=volume_ratio,
                price_change_1m=price_change_1m,
                price_change_3m=price_change_3m,
                price_change_6m=price_change_6m,
                high_52w=high_52w,
                low_52w=low_52w,
                price_pct_from_52w_high=price_pct_from_52w_high,
                price_pct_from_52w_low=price_pct_from_52w_low,
                score=score
            )
        
        except Exception as e:
            self.logger.error(f"Error analyzing technicals for {symbol}: {e}")
            return None
    
    def _calculate_sma(self, df: pd.DataFrame, period: int) -> Optional[float]:
        """Calculate Simple Moving Average"""
        try:
            if len(df) >= period:
                return df['Close'].tail(period).mean()
            return None
        except Exception:
            return None

    def _calculate_ema(self, df: pd.DataFrame, period: int) -> Optional[float]:
        """Calculate Exponential Moving Average"""
        try:
            if len(df) >= period:
                return df['Close'].ewm(span=period, adjust=False).mean().iloc[-1]
            return None
        except Exception:
            return None
    
    def _calculate_rsi(self, df: pd.DataFrame, period: int = 14) -> Optional[float]:
        """Calculate Relative Strength Index"""
        try:
            close = df['Close']
            delta = close.diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
            
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            return rsi.iloc[-1]
        except Exception:
            return None
    
    def _calculate_macd(self, df: pd.DataFrame) -> Optional[Dict[str, float]]:
        """Calculate MACD (Moving Average Convergence Divergence)"""
        try:
            close = df['Close']
            ema_12 = close.ewm(span=12).mean()
            ema_26 = close.ewm(span=26).mean()
            
            macd_line = ema_12 - ema_26
            signal_line = macd_line.ewm(span=9).mean()
            histogram = macd_line - signal_line
            
            return {
                'macd': macd_line.iloc[-1],
                'signal': signal_line.iloc[-1],
                'histogram': histogram.iloc[-1]
            }
        except Exception:
            return None
    
    def _calculate_bollinger_bands(
        self, df: pd.DataFrame, period: int = 20, num_std: float = 2.0
    ) -> Optional[Dict[str, float]]:
        """Calculate Bollinger Bands"""
        try:
            close = df['Close']
            middle = close.rolling(window=period).mean()
            std = close.rolling(window=period).std()
            
            upper = middle + (std * num_std)
            lower = middle - (std * num_std)
            
            return {
                'upper': upper.iloc[-1],
                'middle': middle.iloc[-1],
                'lower': lower.iloc[-1]
            }
        except Exception:
            return None

    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> Optional[float]:
        """Calculate Average True Range (ATR) — a measure of price volatility."""
        try:
            high = df['High']
            low = df['Low']
            close = df['Close']
            prev_close = close.shift(1)
            tr = pd.concat([
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ], axis=1).max(axis=1)
            atr = tr.rolling(window=period).mean()
            return atr.iloc[-1]
        except Exception:
            return None

    def _calculate_volume_ratio(self, df: pd.DataFrame, period: int = 20) -> Optional[float]:
        """Calculate today's volume relative to the N-day average volume."""
        try:
            if 'Volume' not in df.columns or len(df) < period + 1:
                return None
            avg_volume = df['Volume'].iloc[-(period + 1):-1].mean()
            if avg_volume == 0:
                return None
            return df['Volume'].iloc[-1] / avg_volume
        except Exception:
            return None

    def _calculate_price_change(self, df: pd.DataFrame, trading_days: int) -> Optional[float]:
        """Calculate price change % over the given number of trading days."""
        try:
            if len(df) < trading_days + 1:
                return None
            past_price = df['Close'].iloc[-(trading_days + 1)]
            current_price = df['Close'].iloc[-1]
            if past_price == 0:
                return None
            return (current_price - past_price) / past_price
        except Exception:
            return None

    def _calculate_52w_high_low(self, df: pd.DataFrame) -> Tuple[Optional[float], Optional[float]]:
        """Calculate 52-week high and low from available history."""
        try:
            window = df.tail(252)
            high_52w = window['High'].max()
            low_52w = window['Low'].min()
            return high_52w, low_52w
        except Exception:
            return None, None
    
    def _find_support_resistance(self, df: pd.DataFrame) -> Tuple[Optional[float], Optional[float]]:
        """Find support and resistance levels"""
        try:
            close = df['Close']
            low = df['Low']
            high = df['High']
            
            # Support: recent low
            support = low.tail(50).min()
            # Resistance: recent high
            resistance = high.tail(50).max()
            
            return support, resistance
        except Exception:
            return None, None
    
    def _determine_trend(
        self, sma_50: Optional[float], sma_200: Optional[float], 
        current_price: Optional[float]
    ) -> str:
        """Determine current trend"""
        if not all([sma_50, sma_200, current_price]):
            return "unknown"
        
        if current_price > sma_50 > sma_200:
            return "uptrend"
        elif current_price < sma_50 < sma_200:
            return "downtrend"
        else:
            return "sideways"
    
    def _calculate_technical_score(
        self, sma_50, sma_200, rsi, trend, current_price,
        support, resistance, macd=None, volume_ratio=None,
        price_change_1m=None, price_change_3m=None,
        price_pct_from_52w_high=None, atr_pct=None
    ) -> float:
        """
        Calculate technical score (0-100) using competitive screener criteria.

        Scoring factors (aligned with Finviz/StockCharts methodology):
        - Trend / moving average alignment  (Golden/Death cross)
        - RSI momentum (oversold/overbought)
        - MACD histogram direction
        - Price vs Support/Resistance
        - Volume confirmation
        - Price momentum (1-month, 3-month)
        - Position relative to 52-week high (breakout vs near highs)
        - ATR volatility filter
        """
        score = 50
        
        # Trend scoring (primary filter)
        if trend == "uptrend":
            score += 15
        elif trend == "downtrend":
            score -= 15
        
        # RSI scoring (14 period)
        if rsi is not None:
            if 40 <= rsi <= 60:
                score += 5   # Healthy, not extreme
            elif 30 <= rsi < 40:
                score += 8   # Mild pullback — buying zone
            elif rsi < 30:
                score += 4   # Oversold — potential reversal
            elif 60 < rsi <= 70:
                score += 3   # Momentum but not overbought
            else:             # rsi > 70
                score -= 5   # Overbought — risk of pullback

        # MACD histogram direction
        if macd is not None:
            histogram = macd.get("histogram", 0) or 0
            macd_line = macd.get("macd", 0) or 0
            signal_line = macd.get("signal", 0) or 0
            if histogram > 0 and macd_line > signal_line:
                score += 8   # Bullish crossover
            elif histogram > 0:
                score += 4
            elif histogram < 0 and macd_line < signal_line:
                score -= 8   # Bearish crossover
            else:
                score -= 3
        
        # Price vs Support/Resistance (proximity scoring)
        if all([current_price, support, resistance]):
            distance_to_support = current_price - support
            distance_to_resistance = resistance - current_price
            
            if distance_to_support < distance_to_resistance * 0.3:
                score += 8   # Close to support - good entry
            elif distance_to_resistance < distance_to_support * 0.3:
                score -= 8   # Close to resistance - might sell
        
        # Moving averages (Golden cross/Death cross)
        if all([sma_50, sma_200, current_price]):
            if sma_50 > sma_200 and current_price > sma_50:
                score += 8   # Strong uptrend alignment
            elif sma_50 > sma_200:
                score += 3   # Golden cross but price below SMA50
            elif sma_50 < sma_200 and current_price < sma_50:
                score -= 8   # Strong downtrend alignment
            else:
                score -= 3

        # Volume confirmation (above-average volume signals conviction)
        if volume_ratio is not None:
            if volume_ratio >= 1.5:
                score += 5   # Strong volume confirmation
            elif volume_ratio >= 1.2:
                score += 2
            elif volume_ratio < 0.5:
                score -= 3   # Very light volume — weak move

        # Price momentum (3-month is a stronger signal than 1-month)
        if price_change_3m is not None:
            if price_change_3m > 0.20:
                score += 7
            elif price_change_3m > 0.10:
                score += 4
            elif price_change_3m > 0:
                score += 1
            elif price_change_3m < -0.20:
                score -= 7
            else:
                score -= 3

        # 1-month momentum
        if price_change_1m is not None:
            if price_change_1m > 0.10:
                score += 4
            elif price_change_1m > 0.03:
                score += 2
            elif price_change_1m < -0.10:
                score -= 4
            elif price_change_1m < -0.03:
                score -= 2

        # 52-week high proximity (breakout near highs is bullish)
        if price_pct_from_52w_high is not None:
            if price_pct_from_52w_high >= -0.05:
                score += 5   # Within 5% of 52-week high — bullish momentum
            elif price_pct_from_52w_high >= -0.15:
                score += 2
            elif price_pct_from_52w_high < -0.40:
                score -= 4   # Deep below 52-week high — broken trend

        # Volatility penalty (very high ATR% increases risk)
        if atr_pct is not None and atr_pct > 0.05:
            score -= 3  # High daily volatility adds risk

        return max(0, min(100, score))
