"""Market universe module: symbol fetching, caching, and sector filtering.

Provides helpers to build and maintain a broad US stock universe from Wikipedia
sources with disk-backed snapshots and static fallbacks.
"""

from io import StringIO
import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import pandas as pd
import requests
import yfinance as yf

from src.config import config

logger = logging.getLogger(__name__)

# ── HTTP headers ──────────────────────────────────────────────────────────────

WIKIPEDIA_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# ── Static fallback symbol lists ──────────────────────────────────────────────

FALLBACK_SP500_SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "BRK-B", "JPM", "V",
    "MA", "UNH", "XOM", "PG", "JNJ", "HD", "COST", "ABBV", "KO", "PEP",
    "AVGO", "ADBE", "BAC", "CVX", "WMT", "MRK", "AMD", "DIS", "NFLX", "CSCO",
    "CRM", "ABT", "TMO", "MCD", "NKE", "LIN", "ACN", "DHR", "TXN", "INTC",
    "QCOM", "AMGN", "IBM", "GE", "INTU", "SPGI", "NOW", "GS", "PLD", "ISRG",
]

FALLBACK_NASDAQ100_SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "COST", "NFLX",
    "ADBE", "PEP", "CSCO", "AMD", "INTC", "QCOM", "AMGN", "TXN", "INTU", "ISRG",
    "BKNG", "MU", "ADP", "GILD", "SBUX", "LRCX", "MDLZ", "ADI", "PANW", "AMAT",
    "KLAC", "MELI", "SNPS", "CDNS", "CTAS", "FTNT", "CHTR", "CRWD", "MRVL", "ORLY",
    "REGN", "AZN", "PDD", "CSX", "PAYX", "ROST", "MAR", "IDXX", "KDP", "ODFL",
]

# ── Cache TTL constants ───────────────────────────────────────────────────────

MARKET_UNIVERSE_CACHE_TTL_SECONDS = 21600  # 6 hours
MARKET_UNIVERSE_MIN_SP500_COUNT = 400
MARKET_UNIVERSE_MIN_NASDAQ100_COUNT = 80
SYMBOL_SECTOR_CACHE_TTL_SECONDS = 86400  # 24 hours

# ── Cache state ───────────────────────────────────────────────────────────────

_market_universe_cache: dict = {}
_market_universe_cache_lock = threading.Lock()

_symbol_sector_cache: dict = {}
_symbol_sector_cache_lock = threading.Lock()
_symbol_sector_cache_hits = 0
_symbol_sector_cache_misses = 0

_market_universe_snapshot_path = Path(__file__).parent.parent / "data" / "market_universe_snapshot.json"


# ── Symbol normalization ──────────────────────────────────────────────────────

def _normalize_symbol(symbol: str) -> Optional[str]:
    """Normalize one ticker symbol; return None for invalid values."""
    if not symbol:
        return None

    normalized = symbol.strip().upper()
    if not normalized:
        return None

    if len(normalized) > 10:
        return None

    if not re.fullmatch(r"[A-Z0-9.\-]+", normalized):
        return None

    return normalized


def _normalize_symbols(symbols: List[str]) -> List[str]:
    """Normalize, de-duplicate, and filter invalid symbols while preserving order."""
    normalized_symbols = []
    seen: set = set()
    for symbol in symbols:
        normalized = _normalize_symbol(symbol)
        if normalized and normalized not in seen:
            seen.add(normalized)
            normalized_symbols.append(normalized)
    return normalized_symbols


# ── Universe fetching ─────────────────────────────────────────────────────────

def _fetch_symbols_from_wikipedia(url: str, candidate_columns: List[str]) -> List[str]:
    """Fetch ticker symbols from a Wikipedia HTML table."""
    response = requests.get(url, headers=WIKIPEDIA_REQUEST_HEADERS, timeout=10)
    response.raise_for_status()

    tables = pd.read_html(StringIO(response.text))
    for table in tables:
        for column in candidate_columns:
            if column in table.columns:
                symbols = [
                    str(value).strip().upper()
                    for value in table[column].tolist()
                    if not pd.isna(value)
                ]
                cleaned = [_normalize_symbol(s.replace(".", "-")) for s in symbols]
                return [s for s in cleaned if s]
    return []


def _is_valid_universe_size(symbols: List[str], expected_minimum: int) -> bool:
    """Return True when the fetched symbol set looks structurally valid."""
    return len(symbols) >= expected_minimum


def _load_market_universe_snapshot() -> tuple[List[str], List[str]]:
    """Load disk-backed universe snapshot to survive upstream source regressions."""
    def _normalize_snapshot_symbols(values: List[str]) -> List[str]:
        converted = [str(v).replace(".", "-") for v in values]
        return _normalize_symbols(converted)

    try:
        if not _market_universe_snapshot_path.exists():
            return [], []
        payload = json.loads(_market_universe_snapshot_path.read_text(encoding="utf-8"))
        sp500_symbols = _normalize_snapshot_symbols(payload.get("sp500", []))
        nasdaq100_symbols = _normalize_snapshot_symbols(payload.get("nasdaq100", []))
        return sp500_symbols, nasdaq100_symbols
    except Exception as exc:
        logger.warning("Could not load market universe snapshot: %s", exc)
        return [], []


def _save_market_universe_snapshot(sp500_symbols: List[str], nasdaq100_symbols: List[str]) -> None:
    """Persist a known-good universe snapshot for future fallback use."""
    try:
        _market_universe_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp": datetime.now().isoformat(),
            "sp500": _normalize_symbols(sp500_symbols),
            "nasdaq100": _normalize_symbols(nasdaq100_symbols),
        }
        _market_universe_snapshot_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Could not persist market universe snapshot: %s", exc)


def _get_us_market_universe(universe: str) -> List[str]:
    """Build a broad US stock universe with caching and safe fallbacks."""
    universe_key = (universe or "sp500").lower()
    now = datetime.now()

    with _market_universe_cache_lock:
        cached = _market_universe_cache.get(universe_key)
        if cached:
            age_seconds = (now - cached["timestamp"]).total_seconds()
            if age_seconds <= MARKET_UNIVERSE_CACHE_TTL_SECONDS:
                return cached["symbols"]

    try:
        sp500_symbols = _fetch_symbols_from_wikipedia(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            ["Symbol"],
        )
    except Exception as exc:
        logger.warning("Could not fetch S&P 500 symbols from Wikipedia: %s", exc)
        sp500_symbols = []

    try:
        nasdaq100_symbols = _fetch_symbols_from_wikipedia(
            "https://en.wikipedia.org/wiki/Nasdaq-100",
            ["Ticker", "Ticker symbol", "Symbol"],
        )
    except Exception as exc:
        logger.warning("Could not fetch Nasdaq-100 symbols from Wikipedia: %s", exc)
        nasdaq100_symbols = []

    snapshot_sp500, snapshot_nasdaq100 = _load_market_universe_snapshot()

    if not _is_valid_universe_size(sp500_symbols, MARKET_UNIVERSE_MIN_SP500_COUNT):
        if sp500_symbols:
            logger.warning(
                "S&P 500 fetch returned %s symbols; falling back to snapshot/static source",
                len(sp500_symbols),
            )
        sp500_symbols = snapshot_sp500 or FALLBACK_SP500_SYMBOLS

    if not _is_valid_universe_size(nasdaq100_symbols, MARKET_UNIVERSE_MIN_NASDAQ100_COUNT):
        if nasdaq100_symbols:
            logger.warning(
                "Nasdaq-100 fetch returned %s symbols; falling back to snapshot/static source",
                len(nasdaq100_symbols),
            )
        nasdaq100_symbols = snapshot_nasdaq100 or FALLBACK_NASDAQ100_SYMBOLS

    if _is_valid_universe_size(sp500_symbols, MARKET_UNIVERSE_MIN_SP500_COUNT) and _is_valid_universe_size(
        nasdaq100_symbols,
        MARKET_UNIVERSE_MIN_NASDAQ100_COUNT,
    ):
        _save_market_universe_snapshot(sp500_symbols, nasdaq100_symbols)

    if universe_key == "nasdaq100":
        selected = nasdaq100_symbols
    elif universe_key == "combined":
        selected = list(dict.fromkeys(sp500_symbols + nasdaq100_symbols))
    else:
        selected = sp500_symbols

    with _market_universe_cache_lock:
        _market_universe_cache[universe_key] = {
            "timestamp": now,
            "symbols": selected,
        }

    return selected


# ── Sector filtering ──────────────────────────────────────────────────────────

def _normalize_sector(sector: str) -> str:
    """Normalize a sector label to lowercase with collapsed whitespace."""
    return re.sub(r"\s+", " ", sector.strip().lower())


def _resolve_symbol_sector(symbol: str) -> Optional[str]:
    """Resolve sector for one ticker with TTL caching."""
    global _symbol_sector_cache_hits, _symbol_sector_cache_misses

    now = datetime.now()
    with _symbol_sector_cache_lock:
        cached = _symbol_sector_cache.get(symbol)
        if cached:
            age_seconds = (now - cached["timestamp"]).total_seconds()
            if age_seconds <= SYMBOL_SECTOR_CACHE_TTL_SECONDS:
                _symbol_sector_cache_hits += 1
                return cached["sector"]

    try:
        info = yf.Ticker(symbol).info
        sector = info.get("sector") if isinstance(info, dict) else None
        sector = sector.strip() if isinstance(sector, str) else None
    except Exception:
        sector = None

    with _symbol_sector_cache_lock:
        _symbol_sector_cache_misses += 1
        _symbol_sector_cache[symbol] = {
            "timestamp": now,
            "sector": sector,
        }

    return sector


def _filter_symbols_by_sector(symbols: List[str], sector: str) -> List[str]:
    """Filter symbols by sector name using cached Yahoo Finance metadata."""
    target_sector = _normalize_sector(sector)
    if not target_sector or target_sector == "all":
        return symbols

    filtered = []
    max_workers = min(12, max(1, len(symbols)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_resolve_symbol_sector, sym): sym for sym in symbols}
        for future in as_completed(futures):
            sym = futures[future]
            resolved_sector = future.result()
            if resolved_sector and _normalize_sector(resolved_sector) == target_sector:
                filtered.append(sym)

    return filtered


def get_sector_cache_stats() -> dict:
    """Return a snapshot of sector cache counters for the /metrics endpoint."""
    with _symbol_sector_cache_lock:
        hits = _symbol_sector_cache_hits
        misses = _symbol_sector_cache_misses
        size = len(_symbol_sector_cache)
    total = hits + misses
    return {
        "symbol_sector_cache_ttl_seconds": SYMBOL_SECTOR_CACHE_TTL_SECONDS,
        "symbol_sector_cache_size": size,
        "symbol_sector_cache_hits": hits,
        "symbol_sector_cache_misses": misses,
        "symbol_sector_cache_hit_rate_pct": (
            round((hits / total) * 100, 2) if total else 0.0
        ),
    }
