"""Router: market scan endpoints (/fetch-top-performers, /scan-us-market)."""

import asyncio
import logging
import threading
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query

import src.market_universe as market_universe
from src.market_universe import _normalize_sector
from src.models import ScreeningFilter
from src.stock_screener import StockScreener

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Screener injection ────────────────────────────────────────────────────────

_screener: Optional[StockScreener] = None


def set_screener(screener: StockScreener) -> None:
    """Inject the shared StockScreener instance."""
    global _screener
    _screener = screener


def _get_screener() -> StockScreener:
    if _screener is None:
        raise RuntimeError("StockScreener not initialized in market router")
    return _screener


# ── Cache state ───────────────────────────────────────────────────────────────

TOP_PERFORMERS_CACHE_TTL_SECONDS = 300
_top_performers_cache: dict = {}
_top_performers_cache_lock = threading.Lock()
_top_performers_cache_hits = 0
_top_performers_cache_misses = 0

MARKET_SCAN_CACHE_TTL_SECONDS = 900
_market_scan_cache: dict = {}
_market_scan_cache_lock = threading.Lock()
_market_scan_cache_hits = 0
_market_scan_cache_misses = 0


def get_cache_stats() -> dict:
    """Return a snapshot of market-scan and top-performers cache counters."""
    global _top_performers_cache_hits, _top_performers_cache_misses
    global _market_scan_cache_hits, _market_scan_cache_misses

    with _top_performers_cache_lock:
        tp_hits = _top_performers_cache_hits
        tp_misses = _top_performers_cache_misses
        tp_size = len(_top_performers_cache)

    with _market_scan_cache_lock:
        ms_hits = _market_scan_cache_hits
        ms_misses = _market_scan_cache_misses
        ms_size = len(_market_scan_cache)

    with market_universe._market_universe_cache_lock:
        mu_size = len(market_universe._market_universe_cache)

    tp_lookups = tp_hits + tp_misses
    return {
        "top_performers_cache": {
            "ttl_seconds": TOP_PERFORMERS_CACHE_TTL_SECONDS,
            "cache_hits": tp_hits,
            "cache_misses": tp_misses,
            "cache_hit_rate_pct": round((tp_hits / tp_lookups) * 100, 2) if tp_lookups else 0.0,
            "cache_size": tp_size,
        },
        "market_scan_cache": {
            "scan_ttl_seconds": MARKET_SCAN_CACHE_TTL_SECONDS,
            "universe_ttl_seconds": market_universe.MARKET_UNIVERSE_CACHE_TTL_SECONDS,
            "scan_cache_hits": ms_hits,
            "scan_cache_misses": ms_misses,
            "scan_cache_hit_rate_pct": (
                round((ms_hits / (ms_hits + ms_misses)) * 100, 2)
                if (ms_hits + ms_misses)
                else 0.0
            ),
            "scan_cache_size": ms_size,
            "universe_cache_size": mu_size,
            **market_universe.get_sector_cache_stats(),
        },
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/fetch-top-performers")
async def fetch_top_performers(top_n: int = Query(10, ge=1, le=50)):
    """Analyze a curated list of popular stocks and return top picks."""
    global _top_performers_cache_hits, _top_performers_cache_misses

    cache_key = f"top_n={top_n}"
    now = datetime.now()
    with _top_performers_cache_lock:
        cached = _top_performers_cache.get(cache_key)
        if cached:
            age_seconds = (now - cached["timestamp"]).total_seconds()
            if age_seconds <= TOP_PERFORMERS_CACHE_TTL_SECONDS:
                _top_performers_cache_hits += 1
                return cached["payload"]

        _top_performers_cache_misses += 1

    symbols = [
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
        "META", "TSLA", "BRK-B", "JPM", "JNJ",
        "V", "PG", "UNH", "HD", "MA",
    ]

    filters = ScreeningFilter(min_overall_score=0)
    result = _get_screener().screen_stocks(symbols, filters, top_n=top_n)

    payload = {
        "results": result.top_picks,
        "total_candidates": result.total_candidates,
        "filtered_count": result.filtered_count,
        "screening_timestamp": result.screening_timestamp,
    }

    with _top_performers_cache_lock:
        _top_performers_cache[cache_key] = {
            "timestamp": now,
            "payload": payload,
        }

    return payload


@router.get("/scan-us-market")
async def scan_us_market(
    universe: str = Query("sp500", pattern="^(sp500|nasdaq100|combined)$"),
    sector: Optional[str] = Query(None),
    min_overall_score: float = Query(65, ge=0, le=100),
    top_n: int = Query(20, ge=1, le=100),
    max_symbols: int = Query(10, ge=5, le=800),
    seed: Optional[int] = Query(
        None,
        description=(
            "Supply an integer seed to enable deterministic mode. Same inputs plus the same seed "
            "produce stable ordering, and different seeds can change the order of tied scores."
        ),
    ),
):
    """Scan a broad US market universe and return potential opportunities.

    Pass ``seed`` (any integer) to activate deterministic mode: candidate
    symbols are sorted alphabetically before scoring and tied scores are broken
    with a stable seed-derived key. Repeated scans with identical parameters
    and the same seed return the same ordering. When ``seed`` is omitted the
    endpoint behaves dynamically (fresh results on every scan).
    """
    global _market_scan_cache_hits, _market_scan_cache_misses

    normalized_sector = _normalize_sector(sector) if sector else "all"
    cache_key = f"{universe}:{normalized_sector}:{min_overall_score}:{top_n}:{max_symbols}:{seed}"
    now = datetime.now()

    with _market_scan_cache_lock:
        cached = _market_scan_cache.get(cache_key)
        if cached:
            age_seconds = (now - cached["timestamp"]).total_seconds()
            if age_seconds <= MARKET_SCAN_CACHE_TTL_SECONDS:
                _market_scan_cache_hits += 1
                return cached["payload"]
        _market_scan_cache_misses += 1

    symbols = market_universe._get_us_market_universe(universe)[:max_symbols]
    if normalized_sector != "all":
        symbols = await asyncio.to_thread(
            market_universe._filter_symbols_by_sector, symbols, normalized_sector
        )

    if not symbols:
        return {
            "universe": universe,
            "sector": normalized_sector,
            "scanned_count": 0,
            "results": [],
            "total_candidates": 0,
            "filtered_count": 0,
            "screening_timestamp": datetime.now(),
            "deterministic_mode": seed is not None,
            "seed": seed,
        }

    filters = ScreeningFilter(min_overall_score=min_overall_score)
    result = await asyncio.to_thread(
        _get_screener().screen_stocks,
        symbols,
        filters,
        top_n,
        seed,
        True,
    )

    payload = {
        "universe": universe,
        "sector": normalized_sector,
        "scanned_count": len(symbols),
        "results": result.top_picks,
        "total_candidates": result.total_candidates,
        "filtered_count": result.filtered_count,
        "screening_timestamp": result.screening_timestamp,
        "deterministic_mode": result.deterministic_mode,
        "seed": result.seed,
    }

    with _market_scan_cache_lock:
        _market_scan_cache[cache_key] = {
            "timestamp": now,
            "payload": payload,
        }

    return payload
