"""Quality Universe: curated list of ~600 investment-grade stocks.

This module builds and maintains a quality-filtered universe from S&P 500 + Nasdaq-100,
applying fundamental criteria (market cap, liquidity, valuation, financial health).

The filtered list is cached in data/quality_universe.json and refreshed daily.
Scheduler scans all 600 quality stocks with fast pre-screening, then deep-analyzes
the top 300 candidates to balance opportunity coverage with API/compute efficiency.
"""

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

import yfinance as yf

from src.market_universe import _get_us_market_universe, _normalize_symbol

logger = logging.getLogger(__name__)

# ── Cache paths ───────────────────────────────────────────────────────────────

QUALITY_UNIVERSE_PATH = Path(__file__).parent.parent / "data" / "quality_universe.json"
QUALITY_UNIVERSE_CACHE_TTL_SECONDS = 86400  # 24 hours

# ── Quality thresholds ────────────────────────────────────────────────────────

MIN_MARKET_CAP_B = 1.0  # $1B minimum (relaxed)
MIN_PRICE = 1.0  # Lower price floor to include more names
MIN_AVG_VOLUME = 500_000  # 500k shares/day
MAX_PE_RATIO = 200.0  # Allow higher P/E to include growth names
MAX_DEBT_TO_EQUITY = 3.0  # Looser leverage allowance
MIN_PROFIT_MARGIN = -0.50  # Allow larger negative margins for growth names
MIN_ROE = -0.25  # More permissive ROE floor

# ── Cache state ───────────────────────────────────────────────────────────────

_quality_universe_cache: dict = {}
_quality_universe_cache_lock = threading.Lock()


def _load_quality_universe_snapshot() -> Tuple[List[str], datetime]:
    """Load disk-backed quality universe snapshot and its timestamp."""
    try:
        if not QUALITY_UNIVERSE_PATH.exists():
            return [], datetime.fromtimestamp(0)
        payload = json.loads(QUALITY_UNIVERSE_PATH.read_text(encoding="utf-8"))
        symbols = [_normalize_symbol(s) for s in payload.get("symbols", [])]
        symbols = [s for s in symbols if s]  # Filter None values
        timestamp_str = payload.get("timestamp", "")
        timestamp = datetime.fromisoformat(timestamp_str) if timestamp_str else datetime.fromtimestamp(0)
        return symbols, timestamp
    except Exception as exc:
        logger.warning("Could not load quality universe snapshot: %s", exc)
        return [], datetime.fromtimestamp(0)


def _save_quality_universe_snapshot(symbols: List[str]) -> None:
    """Persist quality universe snapshot to disk."""
    try:
        QUALITY_UNIVERSE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp": datetime.now().isoformat(),
            "count": len(symbols),
            "symbols": sorted(set(symbols)),
        }
        QUALITY_UNIVERSE_PATH.write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
        logger.info("Saved quality universe snapshot: %d symbols", len(symbols))
    except Exception as exc:
        logger.error("Could not save quality universe snapshot: %s", exc)


def _assess_stock_quality(symbol: str) -> Tuple[bool, str]:
    """Check if a single stock passes quality criteria.
    
    Returns (passes_quality, reason).
    """
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        
        if not isinstance(info, dict):
            return False, "invalid_info"
        
        # Market cap filter
        market_cap = info.get("marketCap")
        if not market_cap:
            return False, "no_market_cap"
        market_cap_b = market_cap / 1e9
        if market_cap_b < MIN_MARKET_CAP_B:
            return False, f"market_cap_${market_cap_b:.1f}b_lt_{MIN_MARKET_CAP_B}"
        
        # Price filter
        current_price = info.get("currentPrice") or info.get("regularMarketPrice")
        if not current_price or current_price < MIN_PRICE:
            return False, "price_too_low"
        
        # Volume filter
        avg_volume = info.get("averageVolume")
        if not avg_volume or avg_volume < MIN_AVG_VOLUME:
            return False, "low_volume"
        
        # P/E filter (allow None to pass)
        pe_ratio = info.get("trailingPE")
        if pe_ratio is not None and pe_ratio > MAX_PE_RATIO:
            return False, f"pe_{pe_ratio:.1f}_gt_{MAX_PE_RATIO}"
        
        # Debt/Equity filter (allow None to pass)
        debt_to_equity = info.get("debtToEquity")
        if debt_to_equity is not None and debt_to_equity > MAX_DEBT_TO_EQUITY:
            return False, f"de_{debt_to_equity:.2f}_gt_{MAX_DEBT_TO_EQUITY}"
        
        # Profit margin filter (allow None to pass)
        profit_margin = info.get("profitMargins")
        if profit_margin is not None and profit_margin < MIN_PROFIT_MARGIN:
            return False, f"profit_margin_{profit_margin:.2f}_lt_{MIN_PROFIT_MARGIN}"
        
        # ROE filter (allow None to pass)
        roe = info.get("returnOnEquity")
        if roe is not None and roe < MIN_ROE:
            return False, f"roe_{roe:.2f}_lt_{MIN_ROE}"
        
        # All checks passed
        return True, "pass"
    
    except Exception as exc:
        logger.debug("Quality check failed for %s: %s", symbol, exc)
        return False, f"error: {type(exc).__name__}"


def build_quality_universe() -> Tuple[List[str], List[str], List[str]]:
    """Build quality-filtered universes from S&P 500 + Nasdaq-100.
    
    Returns three sorted lists:
    - sp500_quality: ~400 quality S&P 500 stocks
    - nasdaq100_quality: ~100 quality Nasdaq-100 stocks
    - combined_quality: ~500 combined (deduplicated)
    
    Applies parallel quality checks and returns passing symbols.
    """
    logger.info("Building quality universes from S&P 500 + Nasdaq-100...")
    
    # Fetch base universes
    sp500_symbols = _get_us_market_universe("sp500")
    nasdaq100_symbols = _get_us_market_universe("nasdaq100")
    
    logger.info("Base universes: S&P500=%d, Nasdaq100=%d", len(sp500_symbols), len(nasdaq100_symbols))
    
    # Combine all for parallel assessment
    all_symbols = list(dict.fromkeys(sp500_symbols + nasdaq100_symbols))
    logger.info("Total candidates: %d (combined)", len(all_symbols))
    
    # Parallel quality assessment
    quality_map = {}  # symbol -> passes_quality
    rejected_count = 0
    
    max_workers = min(16, max(1, len(all_symbols) // 10))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_assess_stock_quality, sym): sym for sym in all_symbols}
        
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                passes, reason = future.result()
                quality_map[symbol] = passes
                if not passes:
                    rejected_count += 1
                    if rejected_count % 50 == 0:
                        logger.debug("Rejected %d stocks so far...", rejected_count)
            except Exception as exc:
                logger.debug("Exception assessing %s: %s", symbol, exc)
                quality_map[symbol] = False
                rejected_count += 1
    
    # Filter each universe separately
    sp500_quality = sorted([s for s in sp500_symbols if quality_map.get(s)])
    nasdaq100_quality = sorted([s for s in nasdaq100_symbols if quality_map.get(s)])
    combined_quality = sorted(list(dict.fromkeys(sp500_quality + nasdaq100_quality)))
    
    logger.info(
        "Quality universes built: S&P500=%d, Nasdaq100=%d, Combined=%d (rejected: %d)",
        len(sp500_quality), len(nasdaq100_quality), len(combined_quality), rejected_count
    )
    
    # Persist to disk
    _save_quality_universe_snapshot(combined_quality)
    
    return sp500_quality, nasdaq100_quality, combined_quality


def get_quality_universe(universe: str = "combined", force_refresh: bool = False) -> List[str]:
    """Get the cached quality universe, refreshing if needed.
    
    Args:
        universe: "sp500", "nasdaq100", or "combined" (default)
        force_refresh: Force rebuild even if cache is fresh
    
    Returns list of quality-filtered symbols for the specified universe.
    Auto-refreshes if cache is older than 24 hours or force_refresh=True.
    """
    cache_key = f"universe_{universe}"
    
    with _quality_universe_cache_lock:
        cached = _quality_universe_cache.get(cache_key)
        if cached and not force_refresh:
            age_seconds = (datetime.now() - cached["timestamp"]).total_seconds()
            if age_seconds <= QUALITY_UNIVERSE_CACHE_TTL_SECONDS:
                return cached["symbols"]
    
    # Try disk cache first (always combined, so filter if needed)
    disk_symbols, disk_timestamp = _load_quality_universe_snapshot()
    if disk_symbols and not force_refresh:
        age_seconds = (datetime.now() - disk_timestamp).total_seconds()
        if age_seconds <= QUALITY_UNIVERSE_CACHE_TTL_SECONDS:
            # If requesting non-combined, filter from combined
            if universe.lower() == "combined":
                result = disk_symbols
            elif universe.lower() == "sp500":
                sp500_base = _get_us_market_universe("sp500")
                result = [s for s in sp500_base if s in set(disk_symbols)]
            elif universe.lower() == "nasdaq100":
                nasdaq_base = _get_us_market_universe("nasdaq100")
                result = [s for s in nasdaq_base if s in set(disk_symbols)]
            else:
                result = disk_symbols
            
            with _quality_universe_cache_lock:
                _quality_universe_cache[cache_key] = {
                    "timestamp": disk_timestamp,
                    "symbols": result,
                }
            logger.info("Loaded quality universe (%s) from disk cache: %d symbols", universe, len(result))
            return result
    
    # Build fresh
    sp500_quality, nasdaq100_quality, combined_quality = build_quality_universe()
    
    # Select requested universe
    if universe.lower() == "sp500":
        result = sp500_quality
    elif universe.lower() == "nasdaq100":
        result = nasdaq100_quality
    else:
        result = combined_quality
    
    with _quality_universe_cache_lock:
        _quality_universe_cache[cache_key] = {
            "timestamp": datetime.now(),
            "symbols": result,
        }
    
    return result


def get_universe_stats() -> dict:
    """Return statistics about the quality universe."""
    symbols, timestamp = _load_quality_universe_snapshot()
    age_seconds = (datetime.now() - timestamp).total_seconds() if timestamp.year > 1970 else None
    
    return {
        "quality_universe_count": len(symbols),
        "cached_at": timestamp.isoformat() if timestamp.year > 1970 else None,
        "age_seconds": age_seconds,
        "needs_refresh": age_seconds is None or age_seconds > QUALITY_UNIVERSE_CACHE_TTL_SECONDS,
    }


def get_stratified_universe(target_size: int = 300, universe: str = "combined", force_refresh: bool = False) -> List[str]:
    """Return a stratified universe of `target_size` symbols.

    Strategy:
      - Start with the high-quality `combined` list (strict filters).
      - If size < target, fetch remaining candidates from base universe and
        group by sector. Allocate a per-sector quota and pick top market-cap
        names per sector until target is reached. Falls back to global
        market-cap ordering if needed.
    """
    combined_quality = get_quality_universe(force_refresh=force_refresh)
    if len(combined_quality) >= target_size:
        return combined_quality[:target_size]

    # Build pool of remaining candidates
    base = _get_us_market_universe(universe)
    remaining = [s for s in base if s not in set(combined_quality)]

    # Resolve sector and market cap for remaining candidates in parallel
    candidates = []
    def _fetch_info(sym: str):
        try:
            info = yf.Ticker(sym).info
            market_cap = info.get("marketCap") or 0
            sector = info.get("sector") or "Unknown"
            return sym, market_cap, sector
        except Exception:
            return sym, 0, "Unknown"

    max_workers = min(16, max(2, len(remaining) // 10))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_info, s): s for s in remaining}
        for future in as_completed(futures):
            sym, market_cap, sector = future.result()
            candidates.append({"symbol": sym, "market_cap": market_cap or 0, "sector": sector or "Unknown"})

    # Group by sector
    sector_map: dict = {}
    for c in candidates:
        sector = c["sector"]
        sector_map.setdefault(sector, []).append(c)

    # Determine per-sector quota (at least 1 each) proportional to sector size
    total_sectors = len(sector_map) or 1
    quota = max(1, (target_size - len(combined_quality)) // total_sectors)

    selected = list(combined_quality)

    # Pick top market-cap names per sector up to quota
    for sector, items in sector_map.items():
        items_sorted = sorted(items, key=lambda x: x["market_cap"], reverse=True)
        take = min(quota, len(items_sorted))
        for c in items_sorted[:take]:
            if len(selected) >= target_size:
                break
            selected.append(c["symbol"])
        if len(selected) >= target_size:
            break

    # If still short, fill from remaining candidates sorted by market cap
    if len(selected) < target_size:
        remaining_sorted = sorted(candidates, key=lambda x: x["market_cap"], reverse=True)
        for c in remaining_sorted:
            if c["symbol"] in selected:
                continue
            selected.append(c["symbol"])
            if len(selected) >= target_size:
                break

    # Deduplicate and trim
    final = []
    seen = set()
    for s in selected:
        if s not in seen:
            seen.add(s)
            final.append(s)
        if len(final) >= target_size:
            break

    # Persist combined snapshot too (to avoid surprises)
    _save_quality_universe_snapshot(final)
    return final
