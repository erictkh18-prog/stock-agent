"""Trade outcomes module: persistence, summary stats, and learning adjustments.

Provides helpers for logging realized trade outcomes and computing per-symbol
learning adjustments that feed back into the recommendation engine.
"""

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ── Storage path and constants ────────────────────────────────────────────────

TRADE_OUTCOMES_PATH = Path(__file__).parent.parent / "data" / "trade_outcomes.json"

TRADE_OUTCOME_STATUSES = {"target_hit", "stop_hit", "timeout", "manual_close"}

_trade_outcomes_lock = threading.Lock()


# ── Pydantic models ───────────────────────────────────────────────────────────

class TradeOutcomeRequest(BaseModel):
    symbol: str
    outcome: str
    entry_price: float
    exit_price: Optional[float] = None
    target_price: Optional[float] = None
    stop_loss_price: Optional[float] = None
    duration_days: Optional[int] = None
    target_percentage: Optional[float] = None
    recommendation_id: Optional[str] = None
    notes: Optional[str] = None


class TradeOutcomeResponse(BaseModel):
    status: str
    message: str
    record: Dict[str, Any]


# ── Persistence helpers ───────────────────────────────────────────────────────

def _ensure_trade_outcomes_file() -> None:
    """Ensure trade outcome store exists as a JSON list."""
    TRADE_OUTCOMES_PATH.parent.mkdir(parents=True, exist_ok=True)
    if TRADE_OUTCOMES_PATH.exists():
        return
    TRADE_OUTCOMES_PATH.write_text("[]", encoding="utf-8")


def _load_trade_outcomes() -> list[dict]:
    """Load outcomes used for learning from automated closed paper trades only."""
    closed_trades_path = TRADE_OUTCOMES_PATH.parent / "closed_trades.json"
    paper = []
    if closed_trades_path.exists():
        try:
            raw = json.loads(closed_trades_path.read_text(encoding="utf-8"))
            paper = raw if isinstance(raw, list) else []
        except json.JSONDecodeError:
            paper = []

    return paper


def _save_trade_outcomes(records: list[dict]) -> None:
    """Persist trade outcomes to local JSON storage."""
    _ensure_trade_outcomes_file()
    with _trade_outcomes_lock:
        TRADE_OUTCOMES_PATH.write_text(json.dumps(records, indent=2), encoding="utf-8")


# ── Calculation helpers ───────────────────────────────────────────────────────

def _calculate_outcome_return_pct(
    outcome: str,
    entry_price: float,
    exit_price: Optional[float],
    target_price: Optional[float],
    stop_loss_price: Optional[float],
) -> float:
    """Calculate realized return percentage for outcome logs."""
    effective_exit = exit_price
    if effective_exit is None and outcome == "target_hit" and target_price is not None:
        effective_exit = target_price
    if effective_exit is None and outcome == "stop_hit" and stop_loss_price is not None:
        effective_exit = stop_loss_price
    if effective_exit is None:
        return 0.0

    return round(((effective_exit - entry_price) / entry_price) * 100.0, 2)


def _summarize_trade_outcomes(records: list[dict]) -> dict:
    """Build aggregate outcome stats for reporting and UI display."""
    if not records:
        return {
            "total": 0,
            "target_hits": 0,
            "stop_hits": 0,
            "timeouts": 0,
            "manual_closes": 0,
            "win_rate_pct": 0.0,
            "average_return_pct": 0.0,
            "by_symbol": {},
        }

    target_hits = sum(1 for r in records if r.get("outcome") == "target_hit")
    stop_hits = sum(1 for r in records if r.get("outcome") == "stop_hit")
    timeouts = sum(1 for r in records if r.get("outcome") == "timeout")
    manual_closes = sum(1 for r in records if r.get("outcome") == "manual_close")
    avg_return = round(
        sum(float(r.get("return_pct", 0.0)) for r in records) / len(records),
        2,
    )
    win_rate = round((target_hits / len(records)) * 100.0, 2)

    by_symbol: dict[str, dict] = {}
    for record in records:
        symbol = (record.get("symbol") or "").upper()
        if not symbol:
            continue

        bucket = by_symbol.setdefault(
            symbol,
            {
                "total": 0,
                "target_hits": 0,
                "stop_hits": 0,
                "average_return_pct": 0.0,
                "return_sum": 0.0,
            },
        )
        bucket["total"] += 1
        if record.get("outcome") == "target_hit":
            bucket["target_hits"] += 1
        if record.get("outcome") == "stop_hit":
            bucket["stop_hits"] += 1
        bucket["return_sum"] += float(record.get("return_pct", 0.0))

    for symbol, summary in by_symbol.items():
        total = summary["total"]
        summary["average_return_pct"] = round(summary["return_sum"] / total, 2) if total else 0.0
        del summary["return_sum"]

    return {
        "total": len(records),
        "target_hits": target_hits,
        "stop_hits": stop_hits,
        "timeouts": timeouts,
        "manual_closes": manual_closes,
        "win_rate_pct": win_rate,
        "average_return_pct": avg_return,
        "by_symbol": by_symbol,
    }


def _learning_adjustment_for_symbol(symbol: str, outcome_summary: dict) -> float:
    """Return score/upside adjustment for a symbol based on tracked outcomes."""
    symbol_stats = (outcome_summary.get("by_symbol") or {}).get(symbol.upper())
    if not symbol_stats:
        return 0.0

    sample_size = int(symbol_stats.get("total", 0))
    if sample_size < 2:
        return 0.0

    target_hits = float(symbol_stats.get("target_hits", 0))
    stop_hits = float(symbol_stats.get("stop_hits", 0))
    win_rate = target_hits / sample_size
    stop_rate = stop_hits / sample_size
    avg_return = float(symbol_stats.get("average_return_pct", 0.0))

    adjustment = ((win_rate - stop_rate) * 6.0) + (avg_return * 0.12)
    return round(max(-4.0, min(6.0, adjustment)), 2)
