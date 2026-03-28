"""Macro regime detection module.

Uses three independent signals to characterise the current market environment:
  1. SPY trend  — is price above/below its 50d and 200d SMAs?
  2. VIX level  — what is the market's fear gauge reading?
  3. Yield spread — is the 10-year vs 13-week Treasury curve inverted?

Results are cached for 1 hour because macro regime does not change intra-day.
"""

import logging
import threading
from datetime import datetime
from typing import Optional, Tuple

import yfinance as yf

logger = logging.getLogger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────────
_macro_cache: dict = {}
_macro_cache_lock = threading.Lock()
MACRO_CACHE_TTL_SECONDS = 3600  # 1 hour


def get_macro_regime() -> dict:
    """Return current macro regime dict (cached for 1 hour).

    Keys:
        regime          : "bull" | "bear" | "neutral"
        spy_trend       : "uptrend" | "downtrend" | "neutral"
        vix_level       : float | None
        vix_signal      : "low" | "normal" | "elevated" | "high" | "extreme" | "unknown"
        yield_spread    : float | None  (10Y minus 13W; negative = inverted)
        yield_signal    : "normal" | "flat" | "inverted" | "unknown"
        regime_score    : float  (0-100; higher = healthier macro environment)
    """
    now = datetime.now()
    with _macro_cache_lock:
        if "data" in _macro_cache:
            cached_at, data = _macro_cache["data"]
            if (now - cached_at).total_seconds() < MACRO_CACHE_TTL_SECONDS:
                return data

    result = _fetch_macro_data()

    with _macro_cache_lock:
        _macro_cache["data"] = (now, result)

    return result


def _fetch_macro_data() -> dict:
    spy_trend = _get_spy_trend()
    vix_level, vix_signal = _get_vix()
    yield_spread, yield_signal = _get_yield_spread()

    # ── Composite regime score ─────────────────────────────────────────────
    score = 50.0

    # SPY trend is the most important signal (+/-20)
    if spy_trend == "uptrend":
        score += 20
    elif spy_trend == "downtrend":
        score -= 20

    # VIX: complacency is mildly bullish; fear is bearish (+/-15)
    if vix_signal == "low":
        score += 10
    elif vix_signal == "normal":
        score += 4
    elif vix_signal == "elevated":
        score -= 8
    elif vix_signal == "high":
        score -= 15
    elif vix_signal == "extreme":
        score -= 25

    # Yield curve: inversion historically precedes recessions (+/-10)
    if yield_signal == "normal":
        score += 10
    elif yield_signal == "flat":
        score -= 3
    elif yield_signal == "inverted":
        score -= 12

    regime_score = round(max(0.0, min(100.0, score)), 1)

    if regime_score >= 65:
        regime = "bull"
    elif regime_score <= 38:
        regime = "bear"
    else:
        regime = "neutral"

    return {
        "regime": regime,
        "spy_trend": spy_trend,
        "vix_level": vix_level,
        "vix_signal": vix_signal,
        "yield_spread": yield_spread,
        "yield_signal": yield_signal,
        "regime_score": regime_score,
    }


def _get_spy_trend() -> str:
    """Determine SPY trend from 50d vs 200d SMA."""
    try:
        hist = yf.Ticker("SPY").history(period="1y", interval="1d")
        if hist is None or hist.empty or len(hist) < 55:
            return "neutral"
        close = hist["Close"]
        sma50 = float(close.tail(50).mean())
        sma200 = float(close.tail(200).mean()) if len(close) >= 200 else float(close.mean())
        current = float(close.iloc[-1])
        if current > sma50 > sma200:
            return "uptrend"
        if current < sma50 < sma200:
            return "downtrend"
        return "neutral"
    except Exception as exc:
        logger.debug("SPY trend fetch failed: %s", exc)
        return "neutral"


def _get_vix() -> Tuple[Optional[float], str]:
    """Fetch latest VIX closing level."""
    try:
        hist = yf.Ticker("^VIX").history(period="5d", interval="1d")
        if hist is None or hist.empty:
            return None, "unknown"
        level = float(hist["Close"].iloc[-1])
        if level < 15:
            signal = "low"
        elif level < 20:
            signal = "normal"
        elif level < 30:
            signal = "elevated"
        elif level < 40:
            signal = "high"
        else:
            signal = "extreme"
        return round(level, 2), signal
    except Exception as exc:
        logger.debug("VIX fetch failed: %s", exc)
        return None, "unknown"


def _get_yield_spread() -> Tuple[Optional[float], str]:
    """Compute 10-year minus 13-week Treasury spread.

    Negative spread (inverted curve) is a historically reliable recession
    leading indicator with a ~12-18 month lead time.
    """
    try:
        h10 = yf.Ticker("^TNX").history(period="5d", interval="1d")
        h3m = yf.Ticker("^IRX").history(period="5d", interval="1d")
        if h10 is None or h10.empty or h3m is None or h3m.empty:
            return None, "unknown"
        rate10 = float(h10["Close"].iloc[-1])
        rate3m = float(h3m["Close"].iloc[-1])
        spread = round(rate10 - rate3m, 3)
        if spread > 0.75:
            signal = "normal"
        elif spread > 0:
            signal = "flat"
        else:
            signal = "inverted"
        return spread, signal
    except Exception as exc:
        logger.debug("Yield spread fetch failed: %s", exc)
        return None, "unknown"
