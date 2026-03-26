"""Main FastAPI application for Stock Analysis Agent"""
from io import StringIO
import base64
import json
import os
import platform
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
from urllib.parse import urlparse
import pandas as pd
import requests
import yfinance as yf
from bs4 import BeautifulSoup
from pydantic import BaseModel

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
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

WIKIPEDIA_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


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
app.add_middleware(GZipMiddleware, minimum_size=500)

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
MARKET_UNIVERSE_MIN_SP500_COUNT = 400
MARKET_UNIVERSE_MIN_NASDAQ100_COUNT = 80
_market_scan_cache = {}
_market_scan_cache_lock = threading.Lock()
_market_scan_cache_hits = 0
_market_scan_cache_misses = 0
_market_universe_cache = {}
_market_universe_cache_lock = threading.Lock()
_market_universe_snapshot_path = Path(__file__).parent.parent / "data" / "market_universe_snapshot.json"
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

KB_ROOT = Path(__file__).parent.parent / "knowledge-base"
KB_CHANGELOG_PATH = KB_ROOT / "CHANGELOG.md"
KB_MIRROR_BASE_URL = "https://r.jina.ai/"


def _should_rate_limit(path: str) -> bool:
    return (
        path.startswith("/analyze")
        or path.startswith("/screen")
        or path.startswith("/fetch-top-performers")
        or path.startswith("/scan-us-market")
    )


def _slugify(value: str) -> str:
    """Create filesystem-safe slug from user-provided topic text."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    slug = slug.strip("-")
    return slug or "topic"


def _ensure_kb_changelog() -> None:
    """Ensure changelog exists so ingestion can append audit entries."""
    if KB_CHANGELOG_PATH.exists():
        return
    KB_CHANGELOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    KB_CHANGELOG_PATH.write_text("# Knowledge Base Changelog\n", encoding="utf-8")


def _append_kb_changelog(entry: str) -> None:
    """Append ingestion updates to changelog for traceability."""
    _ensure_kb_changelog()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with KB_CHANGELOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"\n- [{timestamp}] {entry}\n")


def _extract_webpage_text(url: str) -> dict:
    """Fetch webpage and return title plus paragraph snippets for ingestion."""
    response = requests.get(url, headers=WIKIPEDIA_REQUEST_HEADERS, timeout=20)

    if response.status_code >= 400:
        if response.status_code in {401, 402, 403, 406, 429, 451}:
            parsed = urlparse(url)
            mirror_candidates = [
                f"{KB_MIRROR_BASE_URL}{url}",
                f"{KB_MIRROR_BASE_URL}http://{parsed.netloc}{parsed.path}{'?' + parsed.query if parsed.query else ''}",
            ]

            for mirror_url in mirror_candidates:
                try:
                    mirror_response = requests.get(
                        mirror_url,
                        headers=WIKIPEDIA_REQUEST_HEADERS,
                        timeout=25,
                    )
                    mirror_response.raise_for_status()
                    text = mirror_response.text
                    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
                    paragraphs = [line for line in lines if len(line) >= 60][:8]
                    return {
                        "title": "Mirror extract",
                        "paragraphs": paragraphs or [text[:600]],
                    }
                except requests.RequestException:
                    continue

            # If all mirrors fail for a blocked source, still return draft content
            # so knowledge-base authoring can continue without hard failure.
            return {
                "title": "Blocked source (manual review required)",
                "paragraphs": [
                    (
                        f"Automated extraction was blocked by the source (HTTP {response.status_code}). "
                        "A draft chapter was created with source metadata only; add summary content manually."
                    )
                ],
            }

        response.raise_for_status()

    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()

    title = (soup.title.string or "").strip() if soup.title else ""
    paragraphs = []
    for paragraph in soup.find_all("p"):
        text = paragraph.get_text(" ", strip=True)
        if len(text) >= 60:
            paragraphs.append(text)
        if len(paragraphs) >= 8:
            break

    if not paragraphs:
        body_text = soup.get_text(" ", strip=True)
        body_text = re.sub(r"\s+", " ", body_text)
        if body_text:
            paragraphs = [body_text[:600]]

    return {
        "title": title or "Untitled source",
        "paragraphs": paragraphs,
    }


def _validate_ingestion_url(url: str) -> None:
    """Allow only HTTP(S) URLs for ingestion to prevent invalid schemes."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Please provide a valid HTTP/HTTPS URL")


def _build_kb_topic_paths(topic: str) -> tuple[Path, Path]:
    """Return topic directory and chapters directory under section/topic/chapter hierarchy."""
    topic_slug = _slugify(topic)
    topic_dir = KB_ROOT / "sections" / "02-trading-domain" / "topics" / f"auto-{topic_slug}"
    chapters_dir = topic_dir / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    return topic_dir, chapters_dir


def _write_topic_index_if_missing(topic_dir: Path, topic: str) -> Path:
    """Create topic-level index file on first ingestion."""
    topic_index = topic_dir / "TOPIC.md"
    if topic_index.exists():
        return topic_index

    topic_index.write_text(
        "\n".join(
            [
                f"# Topic: {topic}",
                "",
                "## Purpose",
                "- Auto-created by knowledge-base builder submissions.",
                "- Keep chapters in Draft until reviewed and promoted.",
                "",
                "## Chapter Folder",
                "- chapters/",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return topic_index


class KnowledgeIngestRequest(BaseModel):
    topic: str
    url: str


class KnowledgeIngestResponse(BaseModel):
    topic: str
    url: str
    source_title: str
    created_chapter: str
    created_topic_index: str
    changelog_updated: str
    status: str
    summary: str


class KnowledgeOpenExplorerRequest(BaseModel):
    path: str


class KnowledgeOpenExplorerResponse(BaseModel):
    status: str
    path: str
    message: str


def _safe_rel_path(path: Path, root: Path) -> str:
    """Return POSIX relative path from root for API payloads."""
    return path.relative_to(root).as_posix()


def _validate_kb_relative_path(relative_path: str) -> Path:
    """Resolve and validate a chapter path inside knowledge-base root."""
    candidate = (KB_ROOT / relative_path).resolve()
    kb_root_resolved = KB_ROOT.resolve()
    if kb_root_resolved not in candidate.parents and candidate != kb_root_resolved:
        raise HTTPException(status_code=400, detail="Invalid chapter path")
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Chapter not found")
    if candidate.suffix.lower() != ".md":
        raise HTTPException(status_code=400, detail="Only markdown chapter files are supported")
    return candidate


def _build_kb_tree() -> dict:
    """Build section/topic/chapter tree for knowledge-base viewer."""
    sections_dir = KB_ROOT / "sections"
    sections = []

    if not sections_dir.exists():
        return {
            "kb_root": KB_ROOT.as_posix(),
            "sections": sections,
            "total_topics": 0,
            "total_chapters": 0,
        }

    total_topics = 0
    total_chapters = 0

    for section_dir in sorted([p for p in sections_dir.iterdir() if p.is_dir()]):
        topics_root = section_dir / "topics"
        topics = []

        if topics_root.exists():
            for topic_dir in sorted([p for p in topics_root.iterdir() if p.is_dir()]):
                chapter_dir = topic_dir / "chapters"
                topic_index_path = topic_dir / "TOPIC.md"

                chapters = []
                if chapter_dir.exists():
                    for chapter_path in sorted(
                        [p for p in chapter_dir.iterdir() if p.is_file() and p.suffix.lower() == ".md"],
                        reverse=True,
                    ):
                        chapters.append(
                            {
                                "name": chapter_path.stem,
                                "relative_path": _safe_rel_path(chapter_path, KB_ROOT),
                                "updated_at": datetime.fromtimestamp(chapter_path.stat().st_mtime).isoformat(),
                            }
                        )

                topics.append(
                    {
                        "name": topic_dir.name,
                        "relative_path": _safe_rel_path(topic_dir, KB_ROOT),
                        "topic_index": _safe_rel_path(topic_index_path, KB_ROOT)
                        if topic_index_path.exists()
                        else None,
                        "chapter_count": len(chapters),
                        "chapters": chapters,
                    }
                )

        total_topics += len(topics)
        total_chapters += sum(topic["chapter_count"] for topic in topics)
        sections.append(
            {
                "name": section_dir.name,
                "relative_path": _safe_rel_path(section_dir, KB_ROOT),
                "topic_count": len(topics),
                "topics": topics,
            }
        )

    return {
        "kb_root": KB_ROOT.as_posix(),
        "sections": sections,
        "total_topics": total_topics,
        "total_chapters": total_chapters,
    }


GITHUB_API_BASE = "https://api.github.com"


def _github_headers() -> dict:
    """Build GitHub API auth headers from GITHUB_TOKEN env var."""
    token = config.GITHUB_TOKEN
    if not token:
        return {}
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _github_commit_files(files: dict[str, str], commit_message: str) -> bool:
    """
    Commit one or more files to GitHub via the Contents API.
    files: mapping of repo-relative POSIX path -> file content (UTF-8 text).
    Returns True on success, False if GITHUB_TOKEN is absent or call fails.
    """
    token = config.GITHUB_TOKEN
    if not token:
        return False

    repo = config.GITHUB_REPO
    branch = config.GITHUB_BRANCH
    headers = _github_headers()
    success = True

    for repo_path, content_text in files.items():
        encoded = base64.b64encode(content_text.encode("utf-8")).decode("ascii")
        api_url = f"{GITHUB_API_BASE}/repos/{repo}/contents/{repo_path}"

        # Fetch current SHA if file already exists (required for updates)
        existing_sha = None
        try:
            get_resp = requests.get(api_url, headers=headers, params={"ref": branch}, timeout=10)
            if get_resp.status_code == 200:
                existing_sha = get_resp.json().get("sha")
        except requests.RequestException:
            pass

        body: dict = {
            "message": commit_message,
            "content": encoded,
            "branch": branch,
        }
        if existing_sha:
            body["sha"] = existing_sha

        try:
            put_resp = requests.put(api_url, headers=headers, json=body, timeout=15)
            if put_resp.status_code not in {200, 201}:
                logger.warning(
                    "GitHub write-back failed for %s: %s %s",
                    repo_path,
                    put_resp.status_code,
                    put_resp.text[:200],
                )
                success = False
        except requests.RequestException as exc:
            logger.warning("GitHub write-back request error for %s: %s", repo_path, exc)
            success = False

    return success


def _open_in_explorer(target: Path) -> None:
    """Open file location in OS explorer/finder for local workflows."""
    if os.name == "nt":
        subprocess.run(["explorer", "/select,", str(target)], check=False)
        return

    if os.name == "posix":
        if platform.system().lower() == "darwin":
            subprocess.run(["open", "-R", str(target)], check=False)
        else:
            subprocess.run(["xdg-open", str(target.parent)], check=False)
        return

    raise RuntimeError("Unsupported operating system for explorer integration")


def _fetch_symbols_from_wikipedia(url: str, candidate_columns: List[str]) -> List[str]:
    """Fetch ticker symbols from a Wikipedia table."""
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
                cleaned = [_normalize_symbol(symbol.replace(".", "-")) for symbol in symbols]
                return [symbol for symbol in cleaned if symbol]
    return []


def _is_valid_universe_size(symbols: List[str], expected_minimum: int) -> bool:
    """Return True when the fetched symbol set looks structurally valid."""
    return len(symbols) >= expected_minimum


def _load_market_universe_snapshot() -> tuple[List[str], List[str]]:
    """Load disk-backed universe snapshot to survive upstream source regressions."""
    def _normalize_snapshot_symbols(values: List[str]) -> List[str]:
        converted = [str(value).replace(".", "-") for value in values]
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

@app.get("/stock-scanner")
async def stock_scanner():
    """Serve the StockPulse Finnhub scanner page"""
    scanner_path = templates_dir / "stock-scanner.html"
    if scanner_path.exists():
        return FileResponse(str(scanner_path))
    return {"message": "Stock scanner not available"}


@app.get("/knowledge-base-builder")
async def knowledge_base_builder():
    """Serve the 2-field knowledge-base ingestion UI."""
    builder_path = templates_dir / "knowledge-base-builder.html"
    if builder_path.exists():
        return FileResponse(str(builder_path))
    return {"message": "Knowledge-base builder UI not available"}


@app.get("/knowledge-base")
async def knowledge_base_viewer():
    """Serve knowledge-base viewer UI."""
    viewer_path = templates_dir / "knowledge-base-viewer.html"
    if viewer_path.exists():
        return FileResponse(str(viewer_path))
    return {"message": "Knowledge-base viewer UI not available"}


@app.get("/knowledge-base/index")
async def knowledge_base_index():
    """Return section/topic/chapter tree for knowledge-base browsing."""
    if not KB_ROOT.exists():
        raise HTTPException(status_code=404, detail="Knowledge base root not found")
    return _build_kb_tree()


@app.get("/knowledge-base/chapter")
async def knowledge_base_chapter(path: str = Query(..., description="Knowledge-base relative markdown path")):
    """Return chapter markdown content and metadata for viewer rendering."""
    if not path.strip():
        raise HTTPException(status_code=400, detail="Chapter path is required")

    chapter_path = _validate_kb_relative_path(path.strip())
    content = chapter_path.read_text(encoding="utf-8")

    return {
        "path": _safe_rel_path(chapter_path, KB_ROOT),
        "title": chapter_path.stem,
        "updated_at": datetime.fromtimestamp(chapter_path.stat().st_mtime).isoformat(),
        "content": content,
    }


@app.post("/knowledge-base/open-explorer", response_model=KnowledgeOpenExplorerResponse)
async def knowledge_base_open_explorer(payload: KnowledgeOpenExplorerRequest):
    """Open chapter file location in local file explorer for quick editing."""
    relative_path = payload.path.strip()
    if not relative_path:
        raise HTTPException(status_code=400, detail="Chapter path is required")

    chapter_path = _validate_kb_relative_path(relative_path)

    try:
        await asyncio.to_thread(_open_in_explorer, chapter_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not open explorer: {exc}") from exc

    return KnowledgeOpenExplorerResponse(
        status="ok",
        path=_safe_rel_path(chapter_path, KB_ROOT),
        message="Explorer opened for chapter path",
    )


@app.post("/knowledge-base/ingest", response_model=KnowledgeIngestResponse)
async def knowledge_base_ingest(payload: KnowledgeIngestRequest):
    """Ingest website content into section/topic/chapter structure (steps 2-5)."""
    topic = payload.topic.strip()
    source_url = payload.url.strip()
    if not topic:
        raise HTTPException(status_code=400, detail="Topic is required")

    _validate_ingestion_url(source_url)

    try:
        extracted = await asyncio.to_thread(_extract_webpage_text, source_url)
    except requests.RequestException as exc:
        raise HTTPException(status_code=400, detail=f"Could not fetch URL: {exc}") from exc

    topic_dir, chapters_dir = _build_kb_topic_paths(topic)
    topic_index = _write_topic_index_if_missing(topic_dir, topic)

    timestamp = datetime.now()
    chapter_name = f"{timestamp.strftime('%Y%m%d-%H%M%S')}-{_slugify(topic)}.md"
    chapter_path = chapters_dir / chapter_name

    paragraphs = extracted.get("paragraphs", [])
    summary = paragraphs[0] if paragraphs else "No substantial text extracted from source."
    claims = paragraphs[:5]

    chapter_lines = [
        "---",
        f"chapter_id: CH-AUTO-{timestamp.strftime('%Y%m%d%H%M%S')}",
        f"title: {topic}",
        "status: Draft",
        "owner: Eric + Copilot",
        f"last_reviewed: {timestamp.strftime('%Y-%m-%d')}",
        "confidence: Medium",
        "sources:",
        f"  - {source_url}",
        "---",
        "",
        "# Objective",
        f"- Capture source knowledge for topic: {topic}.",
        "",
        "# Core Concepts",
        f"- Source title: {extracted.get('title', 'Untitled source')}",
        "",
        "# Extracted Claims",
    ]

    if claims:
        for claim in claims:
            chapter_lines.append(f"- {claim}")
    else:
        chapter_lines.append("- No claim extracted; manual review required.")

    chapter_lines.extend(
        [
            "",
            "# Actionable Rules Derived",
            "- Draft only. Review and promote before production usage.",
            "",
            "# Constraints And Caveats",
            "- Content is automatically extracted and may contain noise.",
            "- Requires human review before status promotion.",
            "",
            "# Implementation Guidance",
            "- This draft supports Step 2 (ingestion) and Step 3 (chapter placement).",
            "- Step 4 applies after chapter is promoted to Approved.",
            "",
            "# References",
            f"- {source_url}",
        ]
    )

    chapter_content = "\n".join(chapter_lines) + "\n"
    chapter_path.write_text(chapter_content, encoding="utf-8")

    topic_index_content = topic_index.read_text(encoding="utf-8")

    _append_kb_changelog(
        f"Ingested topic '{topic}' from {source_url} into {chapter_path.as_posix()}"
    )

    # Write-back to GitHub so content persists across deploys on production.
    # Run in background thread; failure is logged but does NOT break the response.
    kb_root_resolved = KB_ROOT.resolve()
    chapter_repo_path = "knowledge-base/" + _safe_rel_path(chapter_path, KB_ROOT)
    topic_index_repo_path = "knowledge-base/" + _safe_rel_path(topic_index, KB_ROOT)
    commit_msg = f"kb: auto-ingest topic '{topic}' from {source_url}"

    def _do_github_writeback() -> None:
        files_to_commit = {
            chapter_repo_path: chapter_content,
            topic_index_repo_path: topic_index_content,
        }
        ok = _github_commit_files(files_to_commit, commit_msg)
        if ok:
            logger.info("GitHub write-back succeeded for topic '%s'", topic)
        else:
            logger.warning(
                "GitHub write-back skipped or failed for topic '%s' (GITHUB_TOKEN configured: %s)",
                topic,
                bool(config.GITHUB_TOKEN),
            )

    threading.Thread(target=_do_github_writeback, daemon=True).start()

    return KnowledgeIngestResponse(
        topic=topic,
        url=source_url,
        source_title=extracted.get("title", "Untitled source"),
        created_chapter=chapter_path.as_posix(),
        created_topic_index=topic_index.as_posix(),
        changelog_updated=KB_CHANGELOG_PATH.as_posix(),
        status="Draft chapter created and changelog updated",
        summary=summary,
    )

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
    max_pe_ratio: Optional[float] = Query(None, description="Max trailing P/E ratio"),
    max_forward_pe: Optional[float] = Query(None, description="Max forward P/E ratio"),
    min_dividend_yield: Optional[float] = Query(None, description="Min dividend yield (e.g. 0.02 = 2%)"),
    max_debt_to_equity: Optional[float] = Query(None, description="Max debt-to-equity ratio"),
    min_revenue_growth: Optional[float] = Query(None, description="Min revenue growth (e.g. 0.10 = 10%)"),
    min_roe: Optional[float] = Query(None, description="Min return on equity (e.g. 0.15 = 15%)"),
    min_roa: Optional[float] = Query(None, description="Min return on assets (e.g. 0.05 = 5%)"),
    min_profit_margin: Optional[float] = Query(None, description="Min net profit margin"),
    min_operating_margin: Optional[float] = Query(None, description="Min operating margin"),
    max_peg_ratio: Optional[float] = Query(None, description="Max PEG ratio (e.g. 1.5)"),
    max_pb_ratio: Optional[float] = Query(None, description="Max price-to-book ratio"),
    max_price_to_sales: Optional[float] = Query(None, description="Max price-to-sales ratio"),
    max_ev_ebitda: Optional[float] = Query(None, description="Max EV/EBITDA"),
    min_current_ratio: Optional[float] = Query(None, description="Min current ratio (e.g. 1.0)"),
    min_quick_ratio: Optional[float] = Query(None, description="Min quick ratio (e.g. 0.8)"),
    min_eps: Optional[float] = Query(None, description="Min EPS (e.g. 0 to exclude loss-makers)"),
    min_fcf_yield: Optional[float] = Query(None, description="Min free cash flow yield (e.g. 0.02)"),
    max_beta: Optional[float] = Query(None, description="Max beta (e.g. 1.5 for lower volatility)"),
    min_beta: Optional[float] = Query(None, description="Min beta (e.g. 0.5 for minimum activity)"),
    min_price_change_3m: Optional[float] = Query(None, description="Min 3-month price change (e.g. 0.05 = 5%)"),
    min_price_change_1m: Optional[float] = Query(None, description="Min 1-month price change"),
    min_volume_ratio: Optional[float] = Query(None, description="Min volume ratio vs 20-day avg (e.g. 1.2)"),
    trend: Optional[str] = Query(None, description="Price trend: uptrend, downtrend, or sideways"),
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
            max_forward_pe=max_forward_pe,
            min_dividend_yield=min_dividend_yield,
            max_debt_to_equity=max_debt_to_equity,
            min_revenue_growth=min_revenue_growth,
            min_roe=min_roe,
            min_roa=min_roa,
            min_profit_margin=min_profit_margin,
            min_operating_margin=min_operating_margin,
            max_peg_ratio=max_peg_ratio,
            max_pb_ratio=max_pb_ratio,
            max_price_to_sales=max_price_to_sales,
            max_ev_ebitda=max_ev_ebitda,
            min_current_ratio=min_current_ratio,
            min_quick_ratio=min_quick_ratio,
            min_eps=min_eps,
            min_fcf_yield=min_fcf_yield,
            max_beta=max_beta,
            min_beta=min_beta,
            min_price_change_3m=min_price_change_3m,
            min_price_change_1m=min_price_change_1m,
            min_volume_ratio=min_volume_ratio,
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
    max_pe_ratio: Optional[float] = Query(None, description="Max trailing P/E ratio"),
    max_forward_pe: Optional[float] = Query(None, description="Max forward P/E ratio"),
    min_dividend_yield: Optional[float] = Query(None, description="Min dividend yield (e.g. 0.02 = 2%)"),
    max_debt_to_equity: Optional[float] = Query(None, description="Max debt-to-equity ratio"),
    min_revenue_growth: Optional[float] = Query(None, description="Min revenue growth (e.g. 0.10 = 10%)"),
    min_roe: Optional[float] = Query(None, description="Min return on equity (e.g. 0.15 = 15%)"),
    min_roa: Optional[float] = Query(None, description="Min return on assets (e.g. 0.05 = 5%)"),
    min_profit_margin: Optional[float] = Query(None, description="Min net profit margin"),
    min_operating_margin: Optional[float] = Query(None, description="Min operating margin"),
    max_peg_ratio: Optional[float] = Query(None, description="Max PEG ratio (e.g. 1.5)"),
    max_pb_ratio: Optional[float] = Query(None, description="Max price-to-book ratio"),
    max_price_to_sales: Optional[float] = Query(None, description="Max price-to-sales ratio"),
    max_ev_ebitda: Optional[float] = Query(None, description="Max EV/EBITDA"),
    min_current_ratio: Optional[float] = Query(None, description="Min current ratio (e.g. 1.0)"),
    min_quick_ratio: Optional[float] = Query(None, description="Min quick ratio (e.g. 0.8)"),
    min_eps: Optional[float] = Query(None, description="Min EPS (e.g. 0 to exclude loss-makers)"),
    min_fcf_yield: Optional[float] = Query(None, description="Min free cash flow yield (e.g. 0.02)"),
    max_beta: Optional[float] = Query(None, description="Max beta (e.g. 1.5 for lower volatility)"),
    min_beta: Optional[float] = Query(None, description="Min beta (e.g. 0.5 for minimum activity)"),
    min_price_change_3m: Optional[float] = Query(None, description="Min 3-month price change (e.g. 0.05 = 5%)"),
    min_price_change_1m: Optional[float] = Query(None, description="Min 1-month price change"),
    min_volume_ratio: Optional[float] = Query(None, description="Min volume ratio vs 20-day avg (e.g. 1.2)"),
    trend: Optional[str] = Query(None, description="Price trend: uptrend, downtrend, or sideways"),
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
            max_forward_pe=max_forward_pe,
            min_dividend_yield=min_dividend_yield,
            max_debt_to_equity=max_debt_to_equity,
            min_revenue_growth=min_revenue_growth,
            min_roe=min_roe,
            min_roa=min_roa,
            min_profit_margin=min_profit_margin,
            min_operating_margin=min_operating_margin,
            max_peg_ratio=max_peg_ratio,
            max_pb_ratio=max_pb_ratio,
            max_price_to_sales=max_price_to_sales,
            max_ev_ebitda=max_ev_ebitda,
            min_current_ratio=min_current_ratio,
            min_quick_ratio=min_quick_ratio,
            min_eps=min_eps,
            min_fcf_yield=min_fcf_yield,
            max_beta=max_beta,
            min_beta=min_beta,
            min_price_change_3m=min_price_change_3m,
            min_price_change_1m=min_price_change_1m,
            min_volume_ratio=min_volume_ratio,
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
    seed: Optional[int] = Query(None, description="Supply an integer seed to enable deterministic mode. Same inputs plus the same seed produce stable ordering, and different seeds can change the order of tied scores."),
):
    """Scan a broad US market universe and return potential opportunities.

    Pass ``seed`` (any integer) to activate deterministic mode: candidate
    symbols are sorted alphabetically before scoring and tied scores are broken
    with a stable seed-derived key. Repeated scans with identical parameters
    and the same seed return the same ordering. Different seeds can change the
    order of tied names only; the primary score ranking is unchanged. When
    ``seed`` is omitted the endpoint behaves as before (dynamic/fresh results
    on every scan).
    """
    global _market_scan_cache_hits, _market_scan_cache_misses

    normalized_sector = _normalize_sector(sector) if sector else "all"
    # Include the seed in the cache key so deterministic and non-deterministic
    # requests are cached independently and existing clients are not affected.
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
            "deterministic_mode": seed is not None,
            "seed": seed,
        }

    filters = ScreeningFilter(min_overall_score=min_overall_score)
    result = await asyncio.to_thread(
        screener.screen_stocks,
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
