"""Main FastAPI application for Stock Analysis Agent.

This module wires together all feature modules and routers:
- market_universe: symbol fetching, caching, and sector filtering
- knowledge_base:  KB ingestion, auto-research, and chapter management
- trade_outcomes:  trade outcome persistence and summary helpers
- recommendations: recommendation building and scan job management

Each router lives in src/routers/ and handles one feature area.
"""

import os
import subprocess
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Optional
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.config import config
from src.stock_screener import StockScreener

# ── Feature modules (imported for monkeypatching convenience in tests) ────────
import src.market_universe as _market_universe_module
import src.knowledge_base as _knowledge_base_module
import src.trade_outcomes as _trade_outcomes_module

# ── Routers ───────────────────────────────────────────────────────────────────
from src.routers import analysis as _analysis_router
from src.routers import market as _market_router
from src.routers import recommendations as _recommendations_router
from src.routers import knowledge_base as _kb_router
from src.routers import auth as _auth_router
from src.routers import trade_outcomes as _trade_outcomes_router

# ── Configure logging ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ── Version info ──────────────────────────────────────────────────────────────

def _resolve_commit_hash() -> str:
    """Resolve commit hash from platform env vars, then fallback to local git."""
    env_commit = os.getenv("RENDER_GIT_COMMIT") or os.getenv("COMMIT_SHA")
    if env_commit:
        return env_commit[:12]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True, timeout=2,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


APP_VERSION = "1.0.0"
COMMIT_HASH = _resolve_commit_hash()


# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Stock Analysis Agent",
    description="AI-powered stock analysis and screening tool",
    version=APP_VERSION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=500)

static_dir = Path(__file__).parent.parent / "web" / "static"
templates_dir = Path(__file__).parent.parent / "web" / "templates"

if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ── Shared screener (single instance used by all routers) ─────────────────────

screener = StockScreener()

_analysis_router.set_screener(screener)
_market_router.set_screener(screener)
_recommendations_router.set_screener(screener)


# ── Rate limiting ─────────────────────────────────────────────────────────────

RATE_LIMIT_REQUESTS = 30
RATE_LIMIT_WINDOW_SECONDS = 60
_request_windows: dict = defaultdict(deque)
_rate_limit_lock = threading.Lock()


def _should_rate_limit(path: str) -> bool:
    return (
        path.startswith("/analyze")
        or path.startswith("/screen")
        or path.startswith("/fetch-top-performers")
        or path.startswith("/scan-us-market")
        or path.startswith("/stock-recommendations")
    )


@app.middleware("http")
async def ip_rate_limit_middleware(request: Request, call_next):
    path = request.url.path
    if not _should_rate_limit(path):
        return await call_next(request)

    forwarded_for = request.headers.get("x-forwarded-for", "")
    client_ip = forwarded_for.split(",")[0].strip() if forwarded_for else (
        request.client.host if request.client else "unknown"
    )

    now = datetime.now().timestamp()
    with _rate_limit_lock:
        window = _request_windows[client_ip]
        cutoff = now - RATE_LIMIT_WINDOW_SECONDS
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= RATE_LIMIT_REQUESTS:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Please wait a moment and try again."},
            )
        window.append(now)

    return await call_next(request)


# ── Request timing and metrics ────────────────────────────────────────────────

_request_metrics_lock = threading.Lock()
_request_metrics = {
    "total_requests": 0,
    "total_duration_ms": 0.0,
    "slow_requests": 0,
    "status_counts": defaultdict(int),
    "path_counts": defaultdict(int),
}


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
            request.method, request.url.path, duration_ms,
        )

    response.headers["X-Response-Time-Ms"] = f"{duration_ms:.2f}"
    return response


# ── Register feature routers ──────────────────────────────────────────────────

app.include_router(_analysis_router.router)
app.include_router(_market_router.router)
app.include_router(_recommendations_router.router)
app.include_router(_kb_router.router)
app.include_router(_auth_router.router)
app.include_router(_trade_outcomes_router.router)


# ── Page-serving endpoints ────────────────────────────────────────────────────

@app.get("/")
async def root():
    """Serve the dashboard."""
    dashboard_path = templates_dir / "dashboard.html"
    if dashboard_path.exists():
        return FileResponse(str(dashboard_path))
    return {"message": "Stock Analysis Agent API - Visit /docs for API documentation"}


@app.get("/stock-scanner")
async def stock_scanner():
    """Serve the StockPulse Finnhub scanner page."""
    scanner_path = templates_dir / "stock-scanner.html"
    if scanner_path.exists():
        return FileResponse(str(scanner_path))
    return {"message": "Stock scanner not available"}


@app.get("/login")
async def login_page():
    """Serve the KB Builder login / register page."""
    login_path = templates_dir / "login.html"
    if login_path.exists():
        return FileResponse(str(login_path))
    return {"message": "Login page not available"}


@app.get("/admin/approvals")
async def admin_approvals_page():
    """Serve the admin approvals UI for account request moderation."""
    admin_path = templates_dir / "admin-approvals.html"
    if admin_path.exists():
        return FileResponse(str(admin_path))
    return {"message": "Admin approvals page not available"}


@app.get("/knowledge-base-builder")
async def knowledge_base_builder():
    """Serve the knowledge-base ingestion UI (login required client-side)."""
    builder_path = templates_dir / "knowledge-base-builder.html"
    if builder_path.exists():
        return FileResponse(str(builder_path))
    return {"message": "Knowledge-base builder UI not available"}


@app.get("/knowledge-base")
async def knowledge_base_viewer():
    """Serve the knowledge-base viewer UI."""
    viewer_path = templates_dir / "knowledge-base-viewer.html"
    if viewer_path.exists():
        return FileResponse(str(viewer_path))
    return {"message": "Knowledge-base viewer UI not available"}


# ── Utility endpoints ─────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Health check endpoint."""
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


@app.get("/metrics")
async def metrics():
    """Runtime metrics for request latency and cache effectiveness."""
    with _request_metrics_lock:
        total_requests = _request_metrics["total_requests"]
        total_duration_ms = _request_metrics["total_duration_ms"]
        slow_requests = _request_metrics["slow_requests"]
        status_counts = dict(_request_metrics["status_counts"])
        path_counts = dict(_request_metrics["path_counts"])

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
        **_market_router.get_cache_stats(),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
