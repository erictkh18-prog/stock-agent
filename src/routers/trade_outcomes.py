"""Router: trade outcome endpoints."""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from src.market_universe import _normalize_symbol
import src.trade_outcomes as to_module
from src.trade_outcomes import (
    TRADE_OUTCOME_STATUSES,
    TradeOutcomeRequest,
    TradeOutcomeResponse,
    _calculate_outcome_return_pct,
    _load_trade_outcomes,
    _save_trade_outcomes,
    _summarize_trade_outcomes,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/trade-outcomes", response_model=TradeOutcomeResponse)
async def log_trade_outcome(payload: TradeOutcomeRequest):
    """Store realized trade outcome so recommendations can improve over time."""
    normalized_symbol = _normalize_symbol(payload.symbol)
    if not normalized_symbol:
        raise HTTPException(status_code=400, detail="Invalid symbol format")

    normalized_outcome = payload.outcome.strip().lower()
    if normalized_outcome not in TRADE_OUTCOME_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="Outcome must be one of: target_hit, stop_hit, timeout, manual_close",
        )

    if payload.entry_price <= 0:
        raise HTTPException(status_code=400, detail="Entry price must be greater than zero")

    if payload.exit_price is not None and payload.exit_price <= 0:
        raise HTTPException(status_code=400, detail="Exit price must be greater than zero when provided")

    from datetime import datetime

    records = _load_trade_outcomes()
    now = datetime.now().isoformat()
    return_pct = _calculate_outcome_return_pct(
        normalized_outcome,
        payload.entry_price,
        payload.exit_price,
        payload.target_price,
        payload.stop_loss_price,
    )

    record = {
        "recorded_at": now,
        "symbol": normalized_symbol,
        "outcome": normalized_outcome,
        "entry_price": round(payload.entry_price, 4),
        "exit_price": round(payload.exit_price, 4) if payload.exit_price is not None else None,
        "target_price": round(payload.target_price, 4) if payload.target_price is not None else None,
        "stop_loss_price": round(payload.stop_loss_price, 4) if payload.stop_loss_price is not None else None,
        "duration_days": payload.duration_days,
        "target_percentage": payload.target_percentage,
        "recommendation_id": payload.recommendation_id,
        "notes": payload.notes,
        "return_pct": return_pct,
    }
    records.append(record)
    _save_trade_outcomes(records)

    return TradeOutcomeResponse(
        status="ok",
        message="Trade outcome logged",
        record=record,
    )


@router.get("/trade-outcomes")
async def trade_outcomes(limit: int = Query(200, ge=1, le=5000)):
    """Return recent trade outcomes with aggregate performance summary."""
    records = _load_trade_outcomes()
    ordered = sorted(records, key=lambda item: item.get("recorded_at", ""), reverse=True)
    limited = ordered[:limit]
    return {
        "count": len(limited),
        "summary": _summarize_trade_outcomes(records),
        "records": limited,
    }
