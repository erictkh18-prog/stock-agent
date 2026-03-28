"""Fundamental analysis module for stocks"""
import yfinance as yf
import requests
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from typing import Optional
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from src.models import FundamentalAnalysis

logger = logging.getLogger(__name__)

class FundamentalAnalyzer:
    """Analyzes fundamental metrics of stocks"""
    
    def __init__(self):
        self.logger = logger
        self.quote_backoff_until = None
        self._quote_backoff_lock = threading.Lock()

    @staticmethod
    def _is_missing_value(info: dict, key: str) -> bool:
        """Treat only absent/None values as missing; zero is still valid data."""
        return info.get(key) is None

    def _start_quote_backoff(self, status_code: int) -> None:
        """Pause quote fallback attempts after upstream auth/rate-limit failures."""
        now = datetime.now()
        backoff_minutes = 10 if status_code == 401 else 5
        backoff_until = now + timedelta(minutes=backoff_minutes)

        with self._quote_backoff_lock:
            already_active = self.quote_backoff_until and self.quote_backoff_until > now
            self.quote_backoff_until = backoff_until

        if not already_active:
            self.logger.warning(
                "Disabling Yahoo quote fallback for %s minutes after HTTP %s responses",
                backoff_minutes,
                status_code,
            )

    def _fetch_quote_fallback(self, symbol: str) -> dict:
        """Fetch quote fields from Yahoo quote endpoint when `stock.info` is unavailable."""
        with self._quote_backoff_lock:
            if self.quote_backoff_until and datetime.now() < self.quote_backoff_until:
                return {}

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
            if response.status_code in {401, 429}:
                self._start_quote_backoff(response.status_code)
                return {}
            response.raise_for_status()
            payload = response.json()
            result = payload.get("quoteResponse", {}).get("result", [])
            return result[0] if result else {}
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code in {401, 429}:
                self._start_quote_backoff(status_code)
                return {}
            self.logger.warning(f"Quote fallback failed for {symbol}: {exc}")
            return {}
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
            if response.status_code == 404:
                return {}
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
    
    def analyze(
        self,
        symbol: str,
        stock=None,
        info: Optional[dict] = None,
        enable_web_fallback: bool = True,
    ) -> Optional[FundamentalAnalysis]:
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
            # needs_quote: quote endpoint provides price/quote-style metrics and
            # is only useful here when core valuation fields are missing.
            # needs_web:   web page provides trailingPE, epsTrailingTwelveMonths, dividendYield
            needs_quote = (
                self._is_missing_value(info, 'trailingPE')
                or self._is_missing_value(info, 'trailingEps')
            )
            needs_web = enable_web_fallback and (
                self._is_missing_value(info, 'trailingPE')
                or self._is_missing_value(info, 'trailingEps')
                or self._is_missing_value(info, 'dividendYield')
            )

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
            forward_pe = info.get('forwardPE') or quote.get('forwardPE')
            eps = info.get('trailingEps') or quote.get('epsTrailingTwelveMonths') or web_fallback.get('epsTrailingTwelveMonths')
            eps_forward = info.get('forwardEps') or quote.get('epsForward')
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
            quick_ratio = info.get('quickRatio')
            roa = info.get('returnOnAssets')
            roe = info.get('returnOnEquity')
            peg_ratio = info.get('pegRatio') or quote.get('pegRatio')
            pb_ratio = info.get('priceToBook') or quote.get('priceToBook')
            price_to_sales = info.get('priceToSalesTrailing12Months') or quote.get('priceToSalesTrailing12Months')
            ev_ebitda = info.get('enterpriseToEbitda')
            operating_margin = info.get('operatingMargins')
            beta = info.get('beta') or quote.get('beta')

            # Free cash flow and FCF yield
            free_cash_flow = info.get('freeCashflow')
            market_cap = info.get('marketCap') or quote.get('marketCap')
            fcf_yield: Optional[float] = None
            if free_cash_flow is not None and market_cap and market_cap > 0:
                fcf_yield = free_cash_flow / market_cap

            # EPS growth (trailing vs forward as proxy when available)
            eps_growth: Optional[float] = None
            if eps is not None and eps_forward is not None and eps > 0:
                eps_growth = (eps_forward - eps) / abs(eps)

            # Calculate growth metrics
            # Keep expensive financial-statement lookup only when info is available.
            revenue_growth = self._calculate_revenue_growth(stock) if info else None
            profit_margin = info.get('profitMargins')

            # Stage 1: Advanced quality metrics
            roic = self._calculate_roic(info)
            eps_acceleration = self._calculate_eps_acceleration(stock)
            fcf_conversion = self._calculate_fcf_conversion(info)

            # Item 2: Forward EPS revision — are analysts raising estimates?
            # Positive = consensus is moving up (most validated quant factor in literature).
            eps_forward_revision: Optional[float] = None
            if eps is not None and eps_forward is not None and eps != 0:
                eps_forward_revision = round((eps_forward - eps) / abs(eps), 4)

            # Item 6: Short interest — high short float can signal risk OR squeeze potential
            short_float_pct = info.get('shortPercentOfFloat')   # 0-1 (e.g. 0.05 = 5%)
            short_ratio = info.get('shortRatio')                # days to cover

            # Calculate score (now includes ROIC, EPS acceleration, FCF conversion,
            # forward EPS revision, and short interest)
            score = self._calculate_fundamental_score(
                pe_ratio, forward_pe, eps, dividend_yield, debt_to_equity,
                current_ratio, quick_ratio, roa, roe, revenue_growth,
                profit_margin, operating_margin, peg_ratio, pb_ratio,
                price_to_sales, ev_ebitda, fcf_yield, beta,
                roic=roic, eps_acceleration=eps_acceleration, fcf_conversion=fcf_conversion,
                eps_forward_revision=eps_forward_revision,
                short_float_pct=short_float_pct, short_ratio=short_ratio,
            )
            
            return FundamentalAnalysis(
                pe_ratio=pe_ratio,
                forward_pe=forward_pe,
                eps=eps,
                eps_growth=eps_growth,
                dividend_yield=dividend_yield,
                debt_to_equity=debt_to_equity,
                current_ratio=current_ratio,
                quick_ratio=quick_ratio,
                roa=roa,
                roe=roe,
                revenue_growth=revenue_growth,
                profit_margin=profit_margin,
                operating_margin=operating_margin,
                peg_ratio=peg_ratio,
                pb_ratio=pb_ratio,
                price_to_sales=price_to_sales,
                ev_ebitda=ev_ebitda,
                free_cash_flow=free_cash_flow,
                fcf_yield=fcf_yield,
                beta=beta,
                roic=roic,
                eps_acceleration=eps_acceleration,
                fcf_conversion=fcf_conversion,
                eps_forward_revision=eps_forward_revision,
                short_float_pct=short_float_pct,
                short_ratio=short_ratio,
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

    @staticmethod
    def _calculate_roic(info: dict) -> Optional[float]:
        """Return on Invested Capital = Net Income / (Total Equity + Total Debt - Cash).

        ROIC > 15% signals a company that efficiently allocates capital and
        sustainably compounds shareholder wealth (Warren Buffett's key metric).
        """
        try:
            net_income = info.get('netIncomeToCommon')
            if not net_income:
                return None
            total_debt = info.get('totalDebt') or 0
            total_cash = info.get('totalCash') or 0
            book_value_per_share = info.get('bookValue')
            shares = info.get('sharesOutstanding') or info.get('impliedSharesOutstanding')
            if not (book_value_per_share and shares):
                return None
            total_equity = book_value_per_share * shares
            invested_capital = total_equity + total_debt - total_cash
            if invested_capital <= 0:
                return None
            return round(net_income / invested_capital, 4)
        except Exception:
            return None

    def _calculate_eps_acceleration(self, stock) -> Optional[float]:
        """Measure whether earnings growth is accelerating quarter-over-quarter.

        Returns the change in QoQ growth rate (recent_growth - prior_growth).
        Positive = earnings momentum strengthening; negative = cooling off.
        """
        try:
            qi = stock.quarterly_income_stmt
            if qi is None or not hasattr(qi, 'empty') or qi.empty:
                return None
            net_income_row = None
            for key in ('Net Income', 'NetIncome', 'Net Income Common Stockholders'):
                if key in qi.index:
                    net_income_row = qi.loc[key].dropna()
                    break
            if net_income_row is None or len(net_income_row) < 4:
                return None
            q0 = float(net_income_row.iloc[0])  # most recent quarter
            q1 = float(net_income_row.iloc[1])  # one quarter ago
            q2 = float(net_income_row.iloc[2])  # two quarters ago
            if q1 == 0 or q2 == 0:
                return None
            recent_growth = (q0 - q1) / abs(q1)
            prior_growth = (q1 - q2) / abs(q2)
            return round(recent_growth - prior_growth, 4)
        except Exception:
            return None

    @staticmethod
    def _calculate_fcf_conversion(info: dict) -> Optional[float]:
        """FCF Conversion = Free Cash Flow / Net Income.

        Ratio > 1.0 means the company generates more real cash than reported
        net income — a sign of high earnings quality and conservative accounting.
        Ratio < 0 while net income is positive is a red flag.
        """
        try:
            fcf = info.get('freeCashflow')
            net_income = info.get('netIncomeToCommon')
            if fcf is None or not net_income or net_income <= 0:
                return None
            return round(fcf / net_income, 3)
        except Exception:
            return None

    def _calculate_fundamental_score(
        self, pe_ratio=None, forward_pe=None, eps=None, dividend_yield=None,
        debt_to_equity=None, current_ratio=None, quick_ratio=None,
        roa=None, roe=None, revenue_growth=None, profit_margin=None,
        operating_margin=None, peg_ratio=None, pb_ratio=None,
        price_to_sales=None, ev_ebitda=None, fcf_yield=None, beta=None,
        roic=None, eps_acceleration=None, fcf_conversion=None,
        eps_forward_revision=None, short_float_pct=None, short_ratio=None
    ) -> float:
        """
        Calculate a fundamental score (0-100) using 18 competitive screening criteria.

        Scoring logic aligned with industry-standard screeners (Finviz, Stock Rover,
        ValueSense):
        - Valuation: P/E, Forward P/E, PEG, P/B, P/S, EV/EBITDA
        - Profitability: EPS, ROE, ROA, profit margin, operating margin
        - Growth: revenue growth
        - Financial health: debt-to-equity, current ratio, quick ratio, FCF yield
        - Risk: beta
        - Income: dividend yield
        """
        score = 50  # Start with neutral score

        # --- Valuation (max ±20 pts) ---

        # Trailing P/E (ideal 10-25, penalise very high or negative)
        if pe_ratio is not None and pe_ratio > 0:
            if pe_ratio <= 15:
                score += 8
            elif pe_ratio <= 25:
                score += 5
            elif pe_ratio <= 35:
                score += 0
            else:
                score -= 5

        # Forward P/E (better predictor of value than trailing)
        if forward_pe is not None and forward_pe > 0:
            if forward_pe <= 15:
                score += 5
            elif forward_pe <= 25:
                score += 3
            elif forward_pe > 40:
                score -= 4

        # PEG ratio — combines valuation and growth (< 1.0 = undervalued grower)
        if peg_ratio is not None and peg_ratio > 0:
            if peg_ratio < 1.0:
                score += 7
            elif peg_ratio <= 1.5:
                score += 4
            elif peg_ratio <= 2.0:
                score += 1
            else:
                score -= 3

        # Price-to-book (< 1 = trading below book value; > 5 = expensive)
        if pb_ratio is not None and pb_ratio > 0:
            if pb_ratio < 1.5:
                score += 4
            elif pb_ratio <= 3.0:
                score += 2
            elif pb_ratio > 5.0:
                score -= 3

        # Price-to-sales (< 2 attractive; > 10 stretched)
        if price_to_sales is not None and price_to_sales > 0:
            if price_to_sales < 2:
                score += 3
            elif price_to_sales <= 5:
                score += 1
            elif price_to_sales > 10:
                score -= 3

        # EV/EBITDA (< 10 cheap; > 20 expensive)
        if ev_ebitda is not None and ev_ebitda > 0:
            if ev_ebitda < 10:
                score += 4
            elif ev_ebitda <= 15:
                score += 2
            elif ev_ebitda > 20:
                score -= 3

        # --- Profitability (max ±25 pts) ---

        # EPS (positive earnings are a baseline requirement)
        if eps is not None:
            if eps > 0:
                score += 8
            else:
                score -= 10

        # ROE (> 15% signals efficient use of equity capital)
        if roe is not None:
            if roe > 0.20:
                score += 7
            elif roe > 0.15:
                score += 5
            elif roe > 0.10:
                score += 3
            elif roe < 0:
                score -= 5

        # ROA (> 5% signals asset efficiency)
        if roa is not None:
            if roa > 0.10:
                score += 5
            elif roa > 0.05:
                score += 3
            elif roa < 0:
                score -= 4

        # Net profit margin (> 15% = strong; < 0% = loss-making)
        if profit_margin is not None:
            if profit_margin > 0.20:
                score += 5
            elif profit_margin > 0.10:
                score += 3
            elif profit_margin > 0:
                score += 1
            else:
                score -= 5

        # Operating margin (> 15% = operational efficiency)
        if operating_margin is not None:
            if operating_margin > 0.20:
                score += 4
            elif operating_margin > 0.10:
                score += 2
            elif operating_margin < 0:
                score -= 4

        # --- Growth (max ±10 pts) ---

        # Revenue growth (year-over-year)
        if revenue_growth is not None:
            if revenue_growth > 0.20:
                score += 10
            elif revenue_growth > 0.10:
                score += 7
            elif revenue_growth > 0:
                score += 3
            else:
                score -= 5

        # --- Financial health (max ±20 pts) ---

        # Debt-to-equity (lower is safer)
        if debt_to_equity is not None:
            if debt_to_equity < 0.3:
                score += 7
            elif debt_to_equity < 0.5:
                score += 5
            elif debt_to_equity < 1.0:
                score += 2
            elif debt_to_equity > 2.0:
                score -= 7
            elif debt_to_equity > 1.5:
                score -= 4

        # Current ratio (liquidity safety net)
        if current_ratio is not None:
            if current_ratio >= 2.0:
                score += 5
            elif current_ratio >= 1.5:
                score += 3
            elif current_ratio >= 1.0:
                score += 1
            else:
                score -= 8

        # Quick ratio (more stringent liquidity check)
        if quick_ratio is not None:
            if quick_ratio >= 1.5:
                score += 3
            elif quick_ratio >= 1.0:
                score += 1
            elif quick_ratio < 0.5:
                score -= 5

        # Free cash flow yield (> 3% is attractive)
        if fcf_yield is not None:
            if fcf_yield > 0.05:
                score += 5
            elif fcf_yield > 0.02:
                score += 3
            elif fcf_yield < 0:
                score -= 4

        # --- Income (max +5 pts) ---

        # Dividend yield (income investors reward steady dividends)
        if dividend_yield is not None and dividend_yield > 0:
            if dividend_yield > 0.04:
                score += 5
            elif dividend_yield > 0.02:
                score += 3
            else:
                score += 1

        # --- Risk adjustment (max ±5 pts) ---

        # Beta (moderate beta 0.5-1.5 preferred; very high beta penalised)
        if beta is not None:
            if 0.5 <= beta <= 1.5:
                score += 2
            elif beta > 2.5:
                score -= 4
            elif beta < 0:
                score -= 2

        # --- Stage 1: Advanced Quality Metrics (max ±23 pts) ---

        # ROIC — best single metric for capital allocation efficiency
        # Elite companies (MSFT, AAPL, V) regularly exceed 25%.
        if roic is not None:
            if roic > 0.25:        # Elite capital allocator
                score += 10
            elif roic > 0.15:      # Very good
                score += 7
            elif roic > 0.10:      # Above average
                score += 4
            elif roic > 0:         # Positive but modest
                score += 1
            else:                  # Destroying capital
                score -= 5

        # EPS Acceleration — is earnings momentum improving or cooling?
        # Positive = growth rate speeding up QoQ (very bullish signal).
        if eps_acceleration is not None:
            if eps_acceleration > 0.10:    # Strong acceleration
                score += 8
            elif eps_acceleration > 0:     # Improving trend
                score += 4
            elif eps_acceleration < -0.10: # Sharp deceleration
                score -= 5
            elif eps_acceleration < 0:     # Mild slowdown
                score -= 2

        # FCF Conversion = FCF / Net Income. > 1.0 means real cash > paper profits.
        # High conversion = management cannot easily manipulate these earnings.
        if fcf_conversion is not None:
            if fcf_conversion > 1.2:    # Exceptional cash generation
                score += 5
            elif fcf_conversion >= 0.8: # Solid quality
                score += 3
            elif fcf_conversion >= 0.5: # Acceptable
                score += 1
            elif fcf_conversion < 0:    # Negative FCF despite profits (red flag)
                score -= 4

        # Item 2: Forward EPS Revision — are analysts raising estimates?
        # The single most-validated quant signal in academic literature.
        # Positive revision = buy-side models updating upward = price tends to follow.
        if eps_forward_revision is not None:
            if eps_forward_revision > 0.20:    # Analysts sharply raising estimates
                score += 10
            elif eps_forward_revision > 0.10:  # Meaningfully positive revision
                score += 7
            elif eps_forward_revision > 0:     # Consensus nudging up
                score += 3
            elif eps_forward_revision < -0.20: # Analysts cutting deeply
                score -= 8
            elif eps_forward_revision < -0.10: # Moderate downward revisions
                score -= 5
            elif eps_forward_revision < 0:     # Slight cut
                score -= 2

        # Item 6: Short interest
        # High short float + stock in uptrend = short squeeze potential (positive).
        # High short float + no uptrend = bearish institutional signal (negative).
        # High days-to-cover = hard for shorts to exit = risk of violent squeeze OR
        #   signal that sophisticated players see fundamental problems.
        if short_float_pct is not None:
            if short_float_pct > 0.25:          # Very heavily shorted (>25% of float)
                score -= 5                      # Usually shorted for good reason
            elif short_float_pct > 0.15:        # Elevated short interest
                score -= 2
            elif short_float_pct < 0.03:        # Low short interest (institutional confidence)
                score += 2

        if short_ratio is not None:
            if short_ratio > 10:                # Days to cover > 10 = illiquid squeeze risk
                score -= 3
            elif short_ratio > 7:
                score -= 1

        # Clamp score between 0 and 100
        return max(0, min(100, score))
