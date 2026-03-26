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
            
            # Calculate technical score
            score = self._calculate_technical_score(
                sma_50, sma_200, rsi, trend, current_price, support, resistance
            )
            
            return TechnicalAnalysis(
                sma_50=sma_50,
                sma_200=sma_200,
                rsi=rsi,
                macd=macd,
                bollinger_bands=bollinger_bands,
                support_level=support,
                resistance_level=resistance,
                trend=trend,
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
        support, resistance
    ) -> float:
        """
        Calculate technical score (0-100)
        
        Scoring logic:
        - Uptrend: positive
        - RSI 30-70: healthy, extreme values are risky
        - Price near support: positive (buying opportunity)
        - MACD convergence: monitor
        """
        score = 50
        
        # Trend scoring
        if trend == "uptrend":
            score += 15
        elif trend == "downtrend":
            score -= 15
        
        # RSI scoring (14 period)
        if rsi:
            if 30 <= rsi <= 70:
                score += 10
            elif rsi < 30:
                score += 5  # Oversold - potential bounce
            elif rsi > 70:
                score -= 5  # Overbought
        
        # Price vs Support/Resistance
        if all([current_price, support, resistance]):
            distance_to_support = current_price - support
            distance_to_resistance = resistance - current_price
            
            if distance_to_support < distance_to_resistance * 0.3:
                score += 10  # Close to support - good entry
            elif distance_to_resistance < distance_to_support * 0.3:
                score -= 10  # Close to resistance - might sell
        
        # Moving averages (Golden cross/Death cross)
        if all([sma_50, sma_200, current_price]):
            if sma_50 > sma_200 and current_price > sma_50:
                score += 10
            elif sma_50 < sma_200 and current_price < sma_50:
                score -= 10
        
        return max(0, min(100, score))
