"""Router: stock recommendations endpoints."""

import asyncio
import logging
import threading
from datetime import datetime
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query

import src.market_universe as market_universe
from src.market_universe import _normalize_sector
from src.models import ScreeningFilter
from src.recommendations import (
    _build_recommendation_candidate,
    _recommendation_jobs_lock,
    _recommendation_scan_jobs,
    _recommendation_scan_worker,
    _rank_recommendation_candidates,
    _snapshot_recommendation_job,
)
from src.stock_screener import StockScreener
from src.trade_outcomes import (
    _learning_adjustment_for_symbol,
    _load_trade_outcomes,
    _summarize_trade_outcomes,
)

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
        raise RuntimeError("StockScreener not initialized in recommendations router")
    return _screener


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/stock-recommendations")
async def stock_recommendations(
    universe: str = Query("sp500", pattern="^(sp500|nasdaq100|combined)$"),
    sector: Optional[str] = Query(None),
    min_overall_score: float = Query(50, ge=0, le=100),
    top_n: int = Query(10, ge=1, le=50),
    max_symbols: int = Query(80, ge=5, le=800),
    duration_days: int = Query(30, ge=1, le=365),
    target_percentage: float = Query(8.0, ge=1, le=100),
    seed: Optional[int] = Query(None),
):
    """Recommend stocks with projected upside target within user-selected duration."""
    normalized_sector = _normalize_sector(sector) if sector else "all"
    all_symbols = market_universe._get_us_market_universe(universe)

    if normalized_sector != "all":
        symbols = await asyncio.to_thread(
            market_universe._filter_symbols_by_sector, all_symbols, normalized_sector
        )
    else:
        symbols = all_symbols

    symbols = symbols[:max_symbols]

    if not symbols:
        return {
            "universe": universe,
            "sector": normalized_sector,
            "duration_days": duration_days,
            "target_percentage": target_percentage,
            "scanned_count": 0,
            "recommended_count": 0,
            "results": [],
            "summary": "No symbols available for the selected universe and sector.",
        }

    filters = ScreeningFilter(min_overall_score=min_overall_score)
    screen_result = await asyncio.to_thread(
        _get_screener().screen_stocks,
        symbols,
        filters,
        max(top_n * 5, 25),
        seed,
        True,
    )

    candidates = [
        _build_recommendation_candidate(analysis, duration_days, target_percentage)
        for analysis in screen_result.top_picks
    ]

    outcome_summary = _summarize_trade_outcomes(_load_trade_outcomes())
    for candidate in candidates:
        learning_adj = _learning_adjustment_for_symbol(candidate["symbol"], outcome_summary)
        adjusted_upside = round(candidate["expected_upside_pct"] + learning_adj, 2)
        candidate["learning_adjustment"] = learning_adj
        candidate["adjusted_upside_pct"] = max(0.0, adjusted_upside)
        if learning_adj > 0:
            candidate["reason"] += " Past tracked outcomes for this symbol have been favorable."
        elif learning_adj < 0:
            candidate["reason"] += " Past tracked outcomes for this symbol have been weaker, so confidence is trimmed."

    qualified = [c for c in candidates if c["adjusted_upside_pct"] >= target_percentage]
    ranked = sorted(
        qualified,
        key=lambda item: (item["adjusted_upside_pct"], item["overall_score"], item["confidence"]),
        reverse=True,
    )[:top_n]

    if ranked:
        summary = (
            f"Found {len(ranked)} stocks with projected upside >= {target_percentage:.1f}% "
            f"within {duration_days} days."
        )
    else:
        summary = (
            "No stocks currently meet your requested upside and duration target. "
            "Try lowering target percentage, increasing duration, or broadening the universe."
        )

    return {
        "universe": universe,
        "sector": normalized_sector,
        "duration_days": duration_days,
        "target_percentage": target_percentage,
        "scanned_count": len(symbols),
        "recommended_count": len(ranked),
        "results": ranked,
        "summary": summary,
        "learning": {
            "total_tracked_outcomes": outcome_summary.get("total", 0),
            "win_rate_pct": outcome_summary.get("win_rate_pct", 0.0),
            "average_return_pct": outcome_summary.get("average_return_pct", 0.0),
        },
    }


@router.post("/stock-recommendations/scan/start")
async def start_stock_recommendation_scan(
    universe: str = Query("sp500", pattern="^(sp500|nasdaq100|combined)$"),
    sector: Optional[str] = Query(None),
    duration_days: int = Query(30, ge=1, le=365),
    target_percentage: float = Query(8.0, ge=1, le=100),
):
    """Start progressive recommendation scan; returns job_id for polling."""
    normalized_sector = _normalize_sector(sector) if sector else "all"
    all_symbols = market_universe._get_us_market_universe(universe)

    if normalized_sector != "all":
        symbols = await asyncio.to_thread(
            market_universe._filter_symbols_by_sector, all_symbols, normalized_sector
        )
    else:
        symbols = all_symbols

    if not symbols:
        return {
            "job_id": None,
            "status": "completed",
            "universe": universe,
            "sector": normalized_sector,
            "duration_days": duration_days,
            "target_percentage": target_percentage,
            "scanned_count": 0,
            "total_symbols": 0,
            "found_count": 0,
            "results": [],
            "message": "No symbols available for the selected universe and sector.",
        }

    job_id = str(uuid4())
    now = datetime.now().isoformat()
    with _recommendation_jobs_lock:
        _recommendation_scan_jobs[job_id] = {
            "job_id": job_id,
            "status": "running",
            "stop_requested": False,
            "universe": universe,
            "sector": normalized_sector,
            "duration_days": duration_days,
            "target_percentage": target_percentage,
            "scanned_count": 0,
            "total_symbols": len(symbols),
            "results": [],
            "message": "Scan started.",
            "updated_at": now,
        }

    screener = _get_screener()
    worker = threading.Thread(
        target=_recommendation_scan_worker,
        kwargs={
            "job_id": job_id,
            "screener": screener,
            "symbols": symbols,
            "duration_days": duration_days,
            "target_percentage": target_percentage,
        },
        daemon=True,
    )
    worker.start()

    with _recommendation_jobs_lock:
        return _snapshot_recommendation_job(_recommendation_scan_jobs[job_id])


@router.get("/stock-recommendations/scan/{job_id}")
async def get_stock_recommendation_scan(job_id: str):
    """Poll progressive recommendation scan status and current matches."""
    with _recommendation_jobs_lock:
        job = _recommendation_scan_jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Scan job not found")
        job["updated_at"] = datetime.now().isoformat()
        return _snapshot_recommendation_job(job)


@router.post("/stock-recommendations/scan/{job_id}/stop")
async def stop_stock_recommendation_scan(job_id: str):
    """Request background recommendation scan stop."""
    with _recommendation_jobs_lock:
        job = _recommendation_scan_jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Scan job not found")
        job["stop_requested"] = True
        job["message"] = "Stopping scan..."
        job["updated_at"] = datetime.now().isoformat()
        return _snapshot_recommendation_job(job)
