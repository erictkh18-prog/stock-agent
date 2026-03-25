"""Main FastAPI application for Stock Analysis Agent"""
import os
import re
import subprocess
import threading
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import List, Optional
import pandas as pd
import yfinance as yf

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import logging

from src.config import config
from src.models import StockAnalysis, ScreeningFilter, ScreeningResult
from src.stock_screener import StockScreener

# Configure logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def _resolve_commit_hash() -> str:
    """Resolve commit hash from platform env vars, then fallback to local git."""
    env_commit = os.getenv("RENDER_GIT_COMMIT") or os.getenv("COMMIT_SHA")
    if env_commit:
        return env_commit[:12]

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


APP_VERSION = "1.0.0"
COMMIT_HASH = _resolve_commit_hash()

app = FastAPI(
    title="Stock Analysis Agent",
    description="AI-powered stock analysis and screening tool",
    version=APP_VERSION
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Setup static files and templates
static_dir = Path(__file__).parent.parent / "web" / "static"
templates_dir = Path(__file__).parent.parent / "web" / "templates"

if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

screener = StockScreener()

# Lightweight in-memory rate limit for expensive API endpoints.
RATE_LIMIT_REQUESTS = 30
RATE_LIMIT_WINDOW_SECONDS = 60
_request_windows = defaultdict(deque)
_rate_limit_lock = threading.Lock()

TOP_PERFORMERS_CACHE_TTL_SECONDS = 300
_top_performers_cache = {}
_top_performers_cache_lock = threading.Lock()
_top_performers_cache_hits = 0
_top_performers_cache_misses = 0

MARKET_SCAN_CACHE_TTL_SECONDS = 900
MARKET_UNIVERSE_CACHE_TTL_SECONDS = 21600
_market_scan_cache = {}
_market_scan_cache_lock = threading.Lock()
_market_scan_cache_hits = 0
_market_scan_cache_misses = 0
_market_universe_cache = {}
_market_universe_cache_lock = threading.Lock()
SYMBOL_SECTOR_CACHE_TTL_SECONDS = 86400
_symbol_sector_cache = {}
_symbol_sector_cache_lock = threading.Lock()
_symbol_sector_cache_hits = 0
_symbol_sector_cache_misses = 0

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

_request_metrics_lock = threading.Lock()
_request_metrics = {
    "total_requests": 0,
    "total_duration_ms": 0.0,
    "slow_requests": 0,
    "status_counts": defaultdict(int),
    "path_counts": defaultdict(int),
}


def _should_rate_limit(path: str) -> bool:
    return (
        path.startswith("/analyze")
        or path.startswith("/screen")
        or path.startswith("/fetch-top-performers")
        or path.startswith("/scan-us-market")
    )


def _fetch_symbols_from_wikipedia(url: str, candidate_columns: List[str]) -> List[str]:
    """Fetch ticker symbols from a Wikipedia table."""
    tables = pd.read_html(url)
    for table in tables:
        for column in candidate_columns:
            if column in table.columns:
                symbols = [str(value).strip().upper() for value in table[column].tolist()]
                cleaned = [_normalize_symbol(symbol.replace(".", "-")) for symbol in symbols]
                return [symbol for symbol in cleaned if symbol]
    return []


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
        sp500_symbols = FALLBACK_SP500_SYMBOLS

    try:
        nasdaq100_symbols = _fetch_symbols_from_wikipedia(
            "https://en.wikipedia.org/wiki/Nasdaq-100",
            ["Ticker", "Ticker symbol", "Symbol"],
        )
    except Exception as exc:
        logger.warning("Could not fetch Nasdaq-100 symbols from Wikipedia: %s", exc)
        nasdaq100_symbols = FALLBACK_NASDAQ100_SYMBOLS

    if not sp500_symbols:
        sp500_symbols = FALLBACK_SP500_SYMBOLS
    if not nasdaq100_symbols:
        nasdaq100_symbols = FALLBACK_NASDAQ100_SYMBOLS

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
    seen = set()
    for symbol in symbols:
        normalized = _normalize_symbol(symbol)
        if normalized and normalized not in seen:
            seen.add(normalized)
            normalized_symbols.append(normalized)
    return normalized_symbols


def _normalize_sector(sector: str) -> str:
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
    """Filter symbols by sector name using cached Yahoo metadata."""
    target_sector = _normalize_sector(sector)
    if not target_sector or target_sector == "all":
        return symbols

    filtered = []
    max_workers = min(12, max(1, len(symbols)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_resolve_symbol_sector, symbol): symbol for symbol in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            resolved_sector = future.result()
            if resolved_sector and _normalize_sector(resolved_sector) == target_sector:
                filtered.append(symbol)

    return filtered


@app.middleware("http")
async def ip_rate_limit_middleware(request: Request, call_next):
    path = request.url.path
    if not _should_rate_limit(path):
        return await call_next(request)

    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()
    else:
        client_ip = request.client.host if request.client else "unknown"

    now = datetime.now().timestamp()
    with _rate_limit_lock:
        window = _request_windows[client_ip]
        cutoff = now - RATE_LIMIT_WINDOW_SECONDS
        while window and window[0] < cutoff:
            window.popleft()

        if len(window) >= RATE_LIMIT_REQUESTS:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        "Rate limit exceeded. Please wait a moment and try again."
                    )
                },
            )

        window.append(now)

    return await call_next(request)


@app.middleware("http")
async def request_timing_middleware(request: Request, call_next):
    start_time = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start_time) * 1000

    with _request_metrics_lock:
        _request_metrics["total_requests"] += 1
        _request_metrics["total_duration_ms"] += duration_ms
        _request_metrics["path_counts"][request.url.path] += 1
        _request_metrics["status_counts"][response.status_code] += 1
        if duration_ms >= 1000:
            _request_metrics["slow_requests"] += 1

    if duration_ms >= 1500:
        logger.warning(
            "Slow request: %s %s completed in %.2f ms",
            request.method,
            request.url.path,
            duration_ms,
        )

    response.headers["X-Response-Time-Ms"] = f"{duration_ms:.2f}"
    return response

@app.get("/")
async def root():
    """Serve the dashboard"""
    dashboard_path = templates_dir / "dashboard.html"
    if dashboard_path.exists():
        return FileResponse(str(dashboard_path))
    return {"message": "Stock Analysis Agent API - Visit /docs for API documentation"}

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.now()}


@app.get("/version")
async def version():
    """Return app version metadata for deployment verification."""
    return {
        "service": "stock-agent",
        "version": APP_VERSION,
        "commit": COMMIT_HASH,
        "timestamp": datetime.now(),
    }

@app.get("/analyze/{symbol}", response_model=StockAnalysis)
async def analyze_stock(symbol: str):
    """Analyze one stock and return detailed analysis"""
    try:
        normalized_symbol = _normalize_symbol(symbol)
        if not normalized_symbol:
            raise HTTPException(status_code=400, detail="Invalid symbol format")

        analysis = screener.analyze_stock(normalized_symbol)
        if not analysis:
            raise HTTPException(status_code=404, detail=f"Could not analyze {normalized_symbol}")
        return analysis
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error analyzing {symbol}: {e}")
        raise HTTPException(status_code=500, detail=f"Error analyzing stock: {e}")

@app.post("/screen", response_model=ScreeningResult)
async def screen_stocks(
    symbols: List[str] = Query(..., description="List of stock symbols to screen"),
    min_overall_score: Optional[float] = Query(60, ge=0, le=100),
    max_pe_ratio: Optional[float] = Query(None),
    min_dividend_yield: Optional[float] = Query(None),
    max_debt_to_equity: Optional[float] = Query(None),
    trend: Optional[str] = Query(None),
    top_n: Optional[int] = Query(10, ge=1, le=100),
):
    """Screen multiple stocks and return top candidates"""
    try:
        normalized_symbols = _normalize_symbols(symbols)
        if not normalized_symbols:
            raise HTTPException(status_code=400, detail="No valid symbols provided")

        filters = ScreeningFilter(
            min_overall_score=min_overall_score,
            max_pe_ratio=max_pe_ratio,
            min_dividend_yield=min_dividend_yield,
            max_debt_to_equity=max_debt_to_equity,
            trend=trend,
        )
        return screener.screen_stocks(normalized_symbols, filters, top_n)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error screening stocks: {e}")
        raise HTTPException(status_code=500, detail=f"Error screening stocks: {e}")


@app.post("/screen-async", response_model=ScreeningResult)
async def screen_stocks_async(
    symbols: List[str] = Query(..., description="List of stock symbols to screen"),
    min_overall_score: Optional[float] = Query(60, ge=0, le=100),
    max_pe_ratio: Optional[float] = Query(None),
    min_dividend_yield: Optional[float] = Query(None),
    max_debt_to_equity: Optional[float] = Query(None),
    trend: Optional[str] = Query(None),
    top_n: Optional[int] = Query(10, ge=1, le=100),
):
    """Async-friendly screening endpoint that offloads CPU/network work from event loop."""
    try:
        normalized_symbols = _normalize_symbols(symbols)
        if not normalized_symbols:
            raise HTTPException(status_code=400, detail="No valid symbols provided")

        filters = ScreeningFilter(
            min_overall_score=min_overall_score,
            max_pe_ratio=max_pe_ratio,
            min_dividend_yield=min_dividend_yield,
            max_debt_to_equity=max_debt_to_equity,
            trend=trend,
        )

        return await asyncio.to_thread(
            screener.screen_stocks,
            normalized_symbols,
            filters,
            top_n,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error screening stocks asynchronously: {e}")
        raise HTTPException(status_code=500, detail=f"Error screening stocks: {e}")

@app.get("/analyze-text/{symbol}")
async def analyze_text(symbol: str):
    """Human-friendly text summary for one stock"""
    normalized_symbol = _normalize_symbol(symbol)
    if not normalized_symbol:
        raise HTTPException(status_code=400, detail="Invalid symbol format")

    analysis = screener.analyze_stock(normalized_symbol)
    if not analysis:
        raise HTTPException(status_code=404, detail=f"Could not analyze {normalized_symbol}")

    fund_score = analysis.fundamental.score if analysis.fundamental else "N/A"
    tech_score = analysis.technical.score if analysis.technical else "N/A"
    sentiment_score = analysis.sentiment.score if analysis.sentiment else "N/A"

    return {
        "symbol": analysis.symbol,
        "recommendation": analysis.recommendation,
        "overall_score": analysis.overall_score,
        "summary": (
            f"{analysis.symbol}: {analysis.recommendation} (score {analysis.overall_score}). "
            f"Fundamental {fund_score}, Technical {tech_score}, Sentiment {sentiment_score}."
        )
    }

@app.get("/screen-text")
async def screen_text(symbols: List[str] = Query(..., description="List of stock symbols to screen")):
    """Human-friendly text summary for multiple stocks"""
    normalized_symbols = _normalize_symbols(symbols)
    if not normalized_symbols:
        raise HTTPException(status_code=400, detail="No valid symbols provided")

    filters = ScreeningFilter(min_overall_score=60)
    result = screener.screen_stocks(normalized_symbols, filters, top_n=len(normalized_symbols))

    return {
        "symbols": normalized_symbols,
        "results": [
            {
                "symbol": stock.symbol,
                "recommendation": stock.recommendation,
                "overall_score": stock.overall_score,
                "text": (
                    f"{stock.symbol}: {stock.recommendation} (score {stock.overall_score}). "
                    f"Fundamental {getattr(stock.fundamental, 'score', 'N/A')}, "
                    f"Technical {getattr(stock.technical, 'score', 'N/A')}, "
                    f"Sentiment {getattr(stock.sentiment, 'score', 'N/A')}."
                ),
            }
            for stock in result.top_picks
        ],
    }

@app.get("/fetch-top-performers")
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
    result = screener.screen_stocks(symbols, filters, top_n=top_n)

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


@app.get("/scan-us-market")
async def scan_us_market(
    universe: str = Query("sp500", pattern="^(sp500|nasdaq100|combined)$"),
    sector: Optional[str] = Query(None),
    min_overall_score: float = Query(65, ge=0, le=100),
    top_n: int = Query(20, ge=1, le=100),
    max_symbols: int = Query(80, ge=25, le=800),
):
    """Scan a broad US market universe and return potential opportunities."""
    global _market_scan_cache_hits, _market_scan_cache_misses

    normalized_sector = _normalize_sector(sector) if sector else "all"
    cache_key = f"{universe}:{normalized_sector}:{min_overall_score}:{top_n}:{max_symbols}"
    now = datetime.now()
    with _market_scan_cache_lock:
        cached = _market_scan_cache.get(cache_key)
        if cached:
            age_seconds = (now - cached["timestamp"]).total_seconds()
            if age_seconds <= MARKET_SCAN_CACHE_TTL_SECONDS:
                _market_scan_cache_hits += 1
                return cached["payload"]
        _market_scan_cache_misses += 1

    symbols = _get_us_market_universe(universe)[:max_symbols]
    if normalized_sector != "all":
        symbols = await asyncio.to_thread(_filter_symbols_by_sector, symbols, normalized_sector)

    if not symbols:
        return {
            "universe": universe,
            "sector": normalized_sector,
            "scanned_count": 0,
            "results": [],
            "total_candidates": 0,
            "filtered_count": 0,
            "screening_timestamp": datetime.now(),
        }

    filters = ScreeningFilter(min_overall_score=min_overall_score)
    result = await asyncio.to_thread(screener.screen_stocks, symbols, filters, top_n)

    payload = {
        "universe": universe,
        "sector": normalized_sector,
        "scanned_count": len(symbols),
        "results": result.top_picks,
        "total_candidates": result.total_candidates,
        "filtered_count": result.filtered_count,
        "screening_timestamp": result.screening_timestamp,
    }

    with _market_scan_cache_lock:
        _market_scan_cache[cache_key] = {
            "timestamp": now,
            "payload": payload,
        }

    return payload


@app.get("/metrics")
async def metrics():
    """Runtime metrics for request latency and cache effectiveness."""
    with _request_metrics_lock:
        total_requests = _request_metrics["total_requests"]
        total_duration_ms = _request_metrics["total_duration_ms"]
        slow_requests = _request_metrics["slow_requests"]
        status_counts = dict(_request_metrics["status_counts"])
        path_counts = dict(_request_metrics["path_counts"])

    with _top_performers_cache_lock:
        cache_hits = _top_performers_cache_hits
        cache_misses = _top_performers_cache_misses
        cache_size = len(_top_performers_cache)

    with _market_scan_cache_lock:
        market_scan_hits = _market_scan_cache_hits
        market_scan_misses = _market_scan_cache_misses
        market_scan_cache_size = len(_market_scan_cache)

    with _market_universe_cache_lock:
        market_universe_cache_size = len(_market_universe_cache)

    with _symbol_sector_cache_lock:
        symbol_sector_cache_size = len(_symbol_sector_cache)
        symbol_sector_cache_hits = _symbol_sector_cache_hits
        symbol_sector_cache_misses = _symbol_sector_cache_misses

    top_cache_lookups = cache_hits + cache_misses
    top_cache_hit_rate = (
        round((cache_hits / top_cache_lookups) * 100, 2)
        if top_cache_lookups
        else 0.0
    )

    avg_response_ms = round(total_duration_ms / total_requests, 2) if total_requests else 0.0

    return {
        "service": "stock-agent",
        "timestamp": datetime.now(),
        "http": {
            "total_requests": total_requests,
            "avg_response_ms": avg_response_ms,
            "slow_requests_over_1s": slow_requests,
            "status_counts": status_counts,
            "path_counts": path_counts,
        },
        "screener": screener.get_runtime_stats(),
        "top_performers_cache": {
            "ttl_seconds": TOP_PERFORMERS_CACHE_TTL_SECONDS,
            "cache_hits": cache_hits,
            "cache_misses": cache_misses,
            "cache_hit_rate_pct": top_cache_hit_rate,
            "cache_size": cache_size,
        },
        "market_scan_cache": {
            "scan_ttl_seconds": MARKET_SCAN_CACHE_TTL_SECONDS,
            "universe_ttl_seconds": MARKET_UNIVERSE_CACHE_TTL_SECONDS,
            "scan_cache_hits": market_scan_hits,
            "scan_cache_misses": market_scan_misses,
            "scan_cache_hit_rate_pct": (
                round((market_scan_hits / (market_scan_hits + market_scan_misses)) * 100, 2)
                if (market_scan_hits + market_scan_misses)
                else 0.0
            ),
            "scan_cache_size": market_scan_cache_size,
            "universe_cache_size": market_universe_cache_size,
            "symbol_sector_cache_ttl_seconds": SYMBOL_SECTOR_CACHE_TTL_SECONDS,
            "symbol_sector_cache_size": symbol_sector_cache_size,
            "symbol_sector_cache_hits": symbol_sector_cache_hits,
            "symbol_sector_cache_misses": symbol_sector_cache_misses,
            "symbol_sector_cache_hit_rate_pct": (
                round((symbol_sector_cache_hits / (symbol_sector_cache_hits + symbol_sector_cache_misses)) * 100, 2)
                if (symbol_sector_cache_hits + symbol_sector_cache_misses)
                else 0.0
            ),
        },
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
