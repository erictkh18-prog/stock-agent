"""Router: paper trading endpoints (simulated positions and trades)."""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from src.market_universe import _normalize_symbol

logger = logging.getLogger(__name__)

router = APIRouter()

_screener = None


def set_screener(screener) -> None:
    global _screener
    _screener = screener


# ── Auto-buy trigger ──────────────────────────────────────────────────────────

@router.post("/paper-trading/auto-buy")
async def trigger_auto_buy(
    universe: str = Query("sp500", pattern="^(sp500|nasdaq100|combined)$"),
    duration_days: int = Query(30, ge=1, le=365),
    target_pct: float = Query(8.0, ge=1.0, le=100.0),
    shares: int = Query(10, ge=1, le=10000),
):
    """Scan for the top BUY stock and open a simulated position immediately.

    Identical logic to the daily automated job; useful for manual testing or
    on-demand paper trades outside market hours.
    """
    if _screener is None:
        raise HTTPException(status_code=503, detail="Screener not initialized")

    from src.market_universe import _get_us_market_universe
    from src.models import ScreeningFilter
    from src.recommendations import _build_exit_strategy
    from src.paper_trading import open_position

    symbols = _get_us_market_universe(universe)[:80]
    filters = ScreeningFilter(min_overall_score=50)
    result = await asyncio.to_thread(
        _screener.screen_stocks, symbols, filters, 25, None, True
    )

    buys = [a for a in result.top_picks if a.recommendation == "BUY"]
    if not buys:
        return {
            "status": "no_buy",
            "message": "No BUY recommendations found. No position opened.",
            "scanned": len(result.top_picks),
        }

    top = buys[0]
    exit_strategy = _build_exit_strategy(top.current_price, target_pct)

    pos = open_position(
        symbol=top.symbol,
        shares=shares,
        entry_price=top.current_price,
        target_price=exit_strategy["target_price"],
        stop_loss_price=exit_strategy["stop_loss_price"],
        duration_days=duration_days,
        target_pct=target_pct,
        recommendation_score=round(top.overall_score, 2),
        source="manual_trigger",
    )

    return {
        "status": "ok",
        "message": f"Opened {shares}-share paper position in {top.symbol}",
        "position": pos,
        "score": round(top.overall_score, 2),
        "recommendation": top.recommendation,
    }


# ── Manual close ──────────────────────────────────────────────────────────────

@router.post("/paper-trading/positions/{position_id}/close")
async def close_position_endpoint(
    position_id: str,
    exit_price: Optional[float] = Query(None, gt=0, description="Override exit price; defaults to current market price"),
):
    """Manually close an open position at current market price (or supplied price)."""
    import yfinance as yf
    from src.paper_trading import _load_positions, close_position

    positions = _load_positions()
    pos = next((p for p in positions if p["id"] == position_id), None)
    if pos is None:
        raise HTTPException(status_code=404, detail="Position not found")

    if exit_price is None:
        try:
            hist = yf.Ticker(pos["symbol"]).history(period="1d")
            exit_price = float(hist["Close"].iloc[-1]) if not hist.empty else None
        except Exception:
            exit_price = None

    if exit_price is None:
        raise HTTPException(
            status_code=400,
            detail="Could not fetch current price. Provide exit_price as a query parameter.",
        )

    trade = close_position(position_id, exit_price, "manual")
    return {"status": "ok", "trade": trade}


# ── Check-and-close all positions ─────────────────────────────────────────────

@router.post("/paper-trading/check-positions")
async def check_positions():
    """Run the auto-close check across all open positions right now.

    Closes any position whose current price has reached the target, stop loss,
    or expiry date.
    """
    from src.paper_trading import check_and_close_positions

    closed = await asyncio.to_thread(check_and_close_positions)
    return {
        "status": "ok",
        "closed_count": len(closed),
        "closed": closed,
    }


# ── Read endpoints ────────────────────────────────────────────────────────────

@router.get("/paper-trading/positions")
async def list_positions():
    """Return all open positions with live unrealized P&L."""
    from src.paper_trading import get_open_positions

    positions = await asyncio.to_thread(get_open_positions)
    return {"count": len(positions), "positions": positions}


@router.get("/paper-trading/trades")
async def list_closed_trades(limit: int = Query(200, ge=1, le=5000)):
    """Return closed paper trades with aggregate performance summary."""
    from src.paper_trading import _load_closed_trades, summarize_closed_trades

    records = _load_closed_trades()
    ordered = sorted(records, key=lambda r: r.get("closed_at", ""), reverse=True)
    return {
        "count": len(records),
        "summary": summarize_closed_trades(records),
        "trades": ordered[:limit],
    }
