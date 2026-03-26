"""Fundamental analysis module for stocks"""
import yfinance as yf
import requests
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from src.models import FundamentalAnalysis

logger = logging.getLogger(__name__)

class FundamentalAnalyzer:
    """Analyzes fundamental metrics of stocks"""
    
    def __init__(self):
        self.logger = logger

    def _fetch_quote_fallback(self, symbol: str) -> dict:
        """Fetch quote fields from Yahoo quote endpoint when `stock.info` is unavailable."""
        try:
            response = requests.get(
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
            response.raise_for_status()
            payload = response.json()
            result = payload.get("quoteResponse", {}).get("result", [])
            return result[0] if result else {}
        except Exception as exc:
            self.logger.warning(f"Quote fallback failed for {symbol}: {exc}")
            return {}

    def _parse_float(self, raw_value: Optional[str]) -> Optional[float]:
        """Parse a numeric string like '31.8', '0.41%', or '$7.90' into float."""
        if raw_value is None:
            return None

        value = str(raw_value).strip()
        if not value or value in {"N/A", "-", "--"}:
            return None

        multiplier = 1.0
        if value.endswith('%'):
            multiplier = 0.01
            value = value[:-1]

        value = value.replace(',', '').replace('$', '').replace('x', '').strip()
        try:
            return float(value) * multiplier
        except ValueError:
            return None

    def _fetch_web_fallback(self, symbol: str) -> dict:
        """Fallback fundamentals from stockanalysis.com public stats page."""
        url = f"https://stockanalysis.com/stocks/{symbol.lower()}/statistics/"
        try:
            response = requests.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    )
                },
                timeout=10,
            )
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            values = {}

            for row in soup.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue

                label = cells[0].get_text(" ", strip=True)
                value_cell = cells[1]
                raw = value_cell.get("title") or value_cell.get_text(" ", strip=True)

                if label == "PE Ratio":
                    values["trailingPE"] = self._parse_float(raw)
                elif label == "EPS (TTM)" or label == "Earnings Per Share (EPS)":
                    values["epsTrailingTwelveMonths"] = self._parse_float(raw)
                elif label == "Dividend Yield":
                    values["dividendYield"] = self._parse_float(raw)

            return values
        except Exception as exc:
            self.logger.warning(f"Web fallback failed for {symbol}: {exc}")
            return {}
    
    def analyze(self, symbol: str, stock=None, info: Optional[dict] = None) -> Optional[FundamentalAnalysis]:
        """
        Analyze fundamental metrics for a stock
        
        Args:
            symbol: Stock ticker symbol (e.g., 'AAPL')
        
        Returns:
            FundamentalAnalysis object with metrics
        """
        try:
            # Fetch stock data
            if stock is None:
                stock = yf.Ticker(symbol)

            if info is None:
                try:
                    raw_info = stock.info
                    info = raw_info if isinstance(raw_info, dict) else {}
                except Exception as exc:
                    self.logger.warning(f"Could not fetch info for {symbol}: {exc}")
                    info = {}

            # Only fetch fallbacks for fields not already provided by info,
            # and run both fallback requests concurrently to reduce latency.
            # needs_quote: quote endpoint provides trailingPE, epsTrailingTwelveMonths, pegRatio
            # needs_web:   web page provides trailingPE, epsTrailingTwelveMonths, dividendYield
            needs_quote = not info.get('trailingPE') or not info.get('trailingEps') or not info.get('pegRatio')
            needs_web = not info.get('trailingPE') or not info.get('trailingEps') or not info.get('dividendYield')

            quote: dict = {}
            web_fallback: dict = {}
            if needs_quote or needs_web:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = {}
                    if needs_quote:
                        futures['quote'] = executor.submit(self._fetch_quote_fallback, symbol)
                    if needs_web:
                        futures['web'] = executor.submit(self._fetch_web_fallback, symbol)
                    if 'quote' in futures:
                        try:
                            quote = futures['quote'].result()
                        except Exception:
                            quote = {}
                    if 'web' in futures:
                        try:
                            web_fallback = futures['web'].result()
                        except Exception:
                            web_fallback = {}

            # Extract fundamental metrics
            pe_ratio = info.get('trailingPE') or quote.get('trailingPE') or web_fallback.get('trailingPE')
            eps = info.get('trailingEps') or quote.get('epsTrailingTwelveMonths') or web_fallback.get('epsTrailingTwelveMonths')
            dividend_yield = (
                info.get('dividendYield')
                or quote.get('trailingAnnualDividendYield')
                or quote.get('dividendYield')
                or web_fallback.get('dividendYield')
            )
            # Yahoo may return yield as percent (e.g., 0.41 for 0.41%).
            if dividend_yield is not None and dividend_yield > 0.2:
                dividend_yield = dividend_yield / 100
            debt_to_equity = info.get('debtToEquity')
            current_ratio = info.get('currentRatio')
            roa = info.get('returnOnAssets')
            roe = info.get('returnOnEquity')
            peg_ratio = info.get('pegRatio') or quote.get('pegRatio')
            
            # Calculate growth metrics
            # Keep expensive financial-statement lookup only when info is available.
            revenue_growth = self._calculate_revenue_growth(stock) if info else None
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
