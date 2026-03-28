"""Main FastAPI application for Stock Analysis Agent.

Router-to-feature mapping (aligned with project hierarchy):

Knowledge Base (1)
  1.1 KB Content Contribution  → src/routers/kb_contribution.py
  1.2 KB Content Viewer        → src/routers/kb_viewer.py
  1.3 Admin
      1.3.1 Account Maintenance  → src/routers/auth.py
      1.3.2 KB Content Maintenance → src/routers/kb_admin.py

Stock Screening (2)
  2.1 Single Stock Analysis    → src/routers/stock_analysis.py
  2.2 Multiple Stock Analysis  → src/routers/stock_screening.py
  2.3 Top Performers           → src/routers/market.py
  2.4 Stock Recommendations    → src/routers/recommendations.py
    2.5 Transaction Log          → automated paper trading outcomes

Feature logic (non-HTTP) lives in the corresponding src/ modules:
    src/market_universe.py  src/knowledge_base.py
  src/recommendations.py
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
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.config import config
from src.stock_screener import StockScreener
from src.scheduler import start_scheduler, stop_scheduler

# ── Feature modules (imported for monkeypatching convenience in tests) ────────
import src.market_universe as _market_universe_module
import src.knowledge_base as _knowledge_base_module

# ── Routers ───────────────────────────────────────────────────────────────────
# Knowledge base
from src.routers import kb_contribution as _kb_contribution_router  # 1.1
from src.routers import kb_viewer as _kb_viewer_router               # 1.2
from src.routers import kb_admin as _kb_admin_router                 # 1.3.2
from src.routers import auth as _auth_router                         # 1.3.1

# Stock screening
from src.routers import stock_analysis as _stock_analysis_router     # 2.1
from src.routers import stock_screening as _stock_screening_router   # 2.2
from src.routers import market as _market_router                     # 2.3
from src.routers import recommendations as _recommendations_router   # 2.4
from src.routers import paper_trading as _paper_trading_router        # 3.1 Paper Trading

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


# ── Shared screener (defined early so lifespan can reference it) ──────────────
screener = StockScreener()


@asynccontextmanager
async def _lifespan(app):
    start_scheduler(screener)
    yield
    stop_scheduler()


# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Stock Analysis Agent",
    description="AI-powered stock analysis and screening tool",
    version=APP_VERSION,
)
app.router.lifespan_context = _lifespan

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


# ── Inject shared screener into all routers that need it ──────────────────────
_stock_analysis_router.set_screener(screener)
_stock_screening_router.set_screener(screener)
_market_router.set_screener(screener)
_recommendations_router.set_screener(screener)
_paper_trading_router.set_screener(screener)


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
# Knowledge base
app.include_router(_kb_contribution_router.router)   # 1.1 KB Content Contribution
app.include_router(_kb_viewer_router.router)          # 1.2 KB Content Viewer
app.include_router(_kb_admin_router.router)           # 1.3.2 KB Admin - Content Maintenance
app.include_router(_auth_router.router)               # 1.3.1 Admin - Account Maintenance

# Stock screening
app.include_router(_stock_analysis_router.router)    # 2.1 Single Stock Analysis
app.include_router(_stock_screening_router.router)   # 2.2 Multiple Stock Analysis
app.include_router(_market_router.router)             # 2.3 Top Performers
app.include_router(_recommendations_router.router)    # 2.4 Stock Recommendations
app.include_router(_paper_trading_router.router)       # 3.1 Paper Trading


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


@app.get("/admin")
async def admin_module_page():
    """Serve admin module landing page (auth enforced client-side)."""
    admin_module_path = templates_dir / "admin-module.html"
    if admin_module_path.exists():
        return FileResponse(str(admin_module_path))
    return {"message": "Admin module page not available"}


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
