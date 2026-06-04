"""Router: Market admin — quality universe management.

Handles market-related administrative tasks:
  POST /admin/refresh-quality-universe  — rebuild quality universe (600 stocks)
  GET /admin/quality-universe-stats     — show universe cache status
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from src.auth import UserInfo, require_admin
from src.quality_universe import build_quality_universe, get_universe_stats

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Market Admin"])


@router.post("/admin/refresh-quality-universe")
async def refresh_quality_universe(admin: UserInfo = Depends(require_admin)):
    """Rebuild the quality universe by re-filtering S&P 500 + Nasdaq-100.
    
    This is resource-intensive (parallel API calls) and should be run
    during off-market hours. Returns counts of passing/failing stocks.
    """
    try:
        logger.info("admin: triggering quality universe rebuild")
        
        # Run in background thread to avoid blocking
        sp500_quality, nasdaq100_quality, combined_quality = await asyncio.to_thread(
            build_quality_universe
        )
        
        return {
            "status": "success",
            "sp500_quality_count": len(sp500_quality),
            "nasdaq100_quality_count": len(nasdaq100_quality),
            "combined_quality_count": len(combined_quality),
            "message": f"Quality universe rebuilt: {len(combined_quality)} stocks pass filters",
        }
    except Exception as exc:
        logger.error("quality universe rebuild failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Rebuild failed: {str(exc)}") from exc


@router.get("/admin/quality-universe-stats")
async def quality_universe_stats(admin: UserInfo = Depends(require_admin)):
    """Return current quality universe cache status and metrics."""
    stats = get_universe_stats()
    return {
        "status": "ok",
        "quality_universe_count": stats["quality_universe_count"],
        "cached_at": stats["cached_at"],
        "age_seconds": stats["age_seconds"],
        "needs_refresh": stats["needs_refresh"],
        "refresh_endpoint": "/admin/refresh-quality-universe",
    }
