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


def _resolve_auto_buy_duration_days(duration_days: Optional[int]) -> tuple[int, str]:
    """Resolve hold window for auto-buy.

    Rules:
    - Explicit API input wins.
    - Bear/high-vol regimes use longer holding window (45d).
    - Choppy/high-vol neutral regimes use shorter window (21d).
    - Default baseline is 30d.
    """
    if duration_days is not None:
        return duration_days, "manual"

    try:
        from src.macro_regime import get_macro_regime

        macro = get_macro_regime()
        regime = str(macro.get("regime", "neutral")).lower()
        vix_signal = str(macro.get("vix_signal", "unknown")).lower()

        if regime == "bear" or vix_signal in {"high", "extreme"}:
            return 45, "adaptive_bear_or_high_vol"
        if regime == "neutral" and vix_signal == "elevated":
            return 21, "adaptive_choppy_or_elevated_vol"
        return 30, "adaptive_default"
    except Exception as exc:
        logger.warning("Macro regime unavailable for adaptive duration; using 30d default: %s", exc)
        return 30, "fallback_default"


# ── Auto-buy trigger ──────────────────────────────────────────────────────────

@router.post("/paper-trading/auto-buy")
async def trigger_auto_buy(
    universe: str = Query("combined", pattern="^(sp500|nasdaq100|combined)$"),
    duration_days: Optional[int] = Query(None, ge=1, le=365),
    target_pct: float = Query(8.0, ge=1.0, le=100.0),
    shares: int = Query(10, ge=1, le=10000),
    max_positions: int = Query(5, ge=1, le=20),
):
    """Scan BUY-uptrend stocks and open simulated positions immediately.

    Identical logic to the daily automated job; useful for manual testing or
    on-demand paper trades outside market hours.
    """
    if _screener is None:
        raise HTTPException(status_code=503, detail="Screener not initialized")

    from src.market_universe import _get_us_market_universe
    from src.models import ScreeningFilter
    from src.recommendations import _build_exit_strategy
    from src.paper_trading import (
        assert_persistent_storage_ready_for_trading,
        has_open_position,
        open_position,
    )

    try:
        assert_persistent_storage_ready_for_trading()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    effective_duration_days, duration_source = _resolve_auto_buy_duration_days(duration_days)

    symbols = _get_us_market_universe(universe)[:80]
    filters = ScreeningFilter(min_overall_score=50)
    result = await asyncio.to_thread(
        _screener.screen_stocks, symbols, filters, max(25, max_positions * 8), None, True
    )

    buys = [
        a for a in result.top_picks
        if str(a.recommendation).upper() == "BUY"
        and str(getattr(a.technical, "trend", "")).lower() == "uptrend"
    ]
    if not buys:
        return {
            "status": "no_buy",
            "message": "No BUY uptrend recommendations found. No position opened.",
            "scanned": len(result.top_picks),
        }

    opened_positions = []
    skipped_existing = []

    for analysis in buys:
        if len(opened_positions) >= max_positions:
            break

        symbol = str(analysis.symbol).upper()
        if has_open_position(symbol):
            skipped_existing.append(symbol)
            continue

        exit_strategy = _build_exit_strategy(analysis.current_price, target_pct)
        pos = open_position(
            symbol=symbol,
            shares=shares,
            entry_price=analysis.current_price,
            target_price=exit_strategy["target_price"],
            stop_loss_price=exit_strategy["stop_loss_price"],
            duration_days=effective_duration_days,
            target_pct=target_pct,
            recommendation_score=round(analysis.overall_score, 2),
            source="manual_trigger",
        )
        opened_positions.append(pos)

    if not opened_positions:
        return {
            "status": "no_new_positions",
            "message": "All BUY uptrend candidates already have open positions. No new buys executed.",
            "opened_count": 0,
            "opened_positions": [],
            "skipped_existing_symbols": skipped_existing,
            "duration_days": effective_duration_days,
            "duration_source": duration_source,
        }

    return {
        "status": "ok",
        "message": f"Opened {len(opened_positions)} position(s), {shares} shares each.",
        "opened_count": len(opened_positions),
        "opened_positions": opened_positions,
        "position": opened_positions[0],
        "skipped_existing_symbols": skipped_existing,
        "duration_days": effective_duration_days,
        "duration_source": duration_source,
    }



# ── Check-and-close all positions ─────────────────────────────────────────────

@router.post("/paper-trading/check-positions")
async def check_positions():
    """Run the auto-close check across all open positions right now.

    Closes any position whose current price has reached the target, stop loss,
    or expiry date.
    """
    from src.paper_trading import (
        assert_persistent_storage_ready_for_trading,
        check_and_close_positions,
    )

    try:
        assert_persistent_storage_ready_for_trading()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

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


@router.get("/paper-trading/storage-status")
async def storage_status():
    """Return persistence mode/health for paper trading storage."""
    from src.paper_trading import get_storage_status

    status = await asyncio.to_thread(get_storage_status)
    return status


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
