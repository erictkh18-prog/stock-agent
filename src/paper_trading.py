"""Paper trading module: simulated open positions and automated trade lifecycle.

Open positions are stored in data/positions.json.
Closed trades (with P&L) are stored in data/closed_trades.json.
"""

import json
import logging
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import yfinance as yf

logger = logging.getLogger(__name__)

# ── Storage paths ─────────────────────────────────────────────────────────────

POSITIONS_PATH = Path(__file__).parent.parent / "data" / "positions.json"
CLOSED_TRADES_PATH = Path(__file__).parent.parent / "data" / "closed_trades.json"

_lock = threading.Lock()


# ── Persistence helpers ───────────────────────────────────────────────────────

def _ensure_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("[]", encoding="utf-8")


def _load_positions() -> list[dict]:
    _ensure_file(POSITIONS_PATH)
    with _lock:
        try:
            data = json.loads(POSITIONS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = []
    return data if isinstance(data, list) else []


def _save_positions(records: list[dict]) -> None:
    _ensure_file(POSITIONS_PATH)
    with _lock:
        POSITIONS_PATH.write_text(json.dumps(records, indent=2), encoding="utf-8")


def _load_closed_trades() -> list[dict]:
    _ensure_file(CLOSED_TRADES_PATH)
    with _lock:
        try:
            data = json.loads(CLOSED_TRADES_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = []
    return data if isinstance(data, list) else []


def _save_closed_trades(records: list[dict]) -> None:
    _ensure_file(CLOSED_TRADES_PATH)
    with _lock:
        CLOSED_TRADES_PATH.write_text(json.dumps(records, indent=2), encoding="utf-8")


# ── Position lifecycle ────────────────────────────────────────────────────────

def open_position(
    symbol: str,
    shares: int,
    entry_price: float,
    target_price: float,
    stop_loss_price: float,
    duration_days: int,
    target_pct: float,
    recommendation_score: float,
    source: str = "auto",
) -> dict:
    """Record a new simulated buy and return the position dict."""
    now = datetime.now()
    position = {
        "id": str(uuid.uuid4()),
        "symbol": symbol.upper(),
        "shares": shares,
        "entry_price": round(entry_price, 4),
        "target_price": round(target_price, 4),
        "stop_loss_price": round(stop_loss_price, 4),
        "duration_days": duration_days,
        "target_pct": target_pct,
        "recommendation_score": round(recommendation_score, 2),
        "source": source,
        "opened_at": now.isoformat(),
        "expires_at": (now + timedelta(days=duration_days)).isoformat(),
    }
    positions = _load_positions()
    positions.append(position)
    _save_positions(positions)
    logger.info("Opened position: %s x%d @ $%.2f", symbol.upper(), shares, entry_price)
    return position


def close_position(position_id: str, exit_price: float, exit_reason: str) -> Optional[dict]:
    """Move a position from open → closed trade. Returns the closed trade or None."""
    positions = _load_positions()
    pos = next((p for p in positions if p["id"] == position_id), None)
    if pos is None:
        return None

    return_pct = round(((exit_price - pos["entry_price"]) / pos["entry_price"]) * 100.0, 2)
    pnl = round((exit_price - pos["entry_price"]) * pos["shares"], 2)

    trade = {
        **pos,
        "exit_price": round(exit_price, 4),
        "exit_reason": exit_reason,
        "closed_at": datetime.now().isoformat(),
        "return_pct": return_pct,
        "pnl": pnl,
        # Legacy field so _learning_adjustment_for_symbol can read this file
        "outcome": exit_reason,
        "recorded_at": datetime.now().isoformat(),
    }

    closed = _load_closed_trades()
    closed.append(trade)
    _save_closed_trades(closed)

    remaining = [p for p in positions if p["id"] != position_id]
    _save_positions(remaining)

    logger.info(
        "Closed position: %s @ $%.2f (%s) return=%.2f%% P&L=$%.2f",
        pos["symbol"], exit_price, exit_reason, return_pct, pnl,
    )
    return trade


def check_and_close_positions() -> list[dict]:
    """Fetch current prices for all open positions; close any that hit target/stop/timeout."""
    positions = _load_positions()
    if not positions:
        return []

    closed_trades = []
    now = datetime.now()

    for pos in list(positions):
        symbol = pos["symbol"]
        try:
            hist = yf.Ticker(symbol).history(period="1d")
            if hist.empty:
                continue
            current_price = float(hist["Close"].iloc[-1])
        except Exception as e:
            logger.warning("Could not fetch price for %s: %s", symbol, e)
            continue

        exit_reason = None
        if current_price >= pos["target_price"]:
            exit_reason = "target_hit"
        elif current_price <= pos["stop_loss_price"]:
            exit_reason = "stop_hit"
        elif datetime.fromisoformat(pos["expires_at"]) <= now:
            exit_reason = "timeout"

        if exit_reason:
            trade = close_position(pos["id"], current_price, exit_reason)
            if trade:
                closed_trades.append(trade)

    return closed_trades


def get_open_positions() -> list[dict]:
    """Return all open positions enriched with current price and unrealized P&L."""
    positions = _load_positions()
    result = []
    now = datetime.now()

    for pos in positions:
        current_price = None
        try:
            hist = yf.Ticker(pos["symbol"]).history(period="1d")
            if not hist.empty:
                current_price = float(hist["Close"].iloc[-1])
        except Exception:
            pass

        days_held = (now - datetime.fromisoformat(pos["opened_at"])).days
        days_remaining = max(0, pos["duration_days"] - days_held)
        unrealized_pct = None
        unrealized_pnl = None
        if current_price is not None:
            unrealized_pct = round(
                ((current_price - pos["entry_price"]) / pos["entry_price"]) * 100.0, 2
            )
            unrealized_pnl = round((current_price - pos["entry_price"]) * pos["shares"], 2)

        result.append({
            **pos,
            "current_price": current_price,
            "unrealized_pct": unrealized_pct,
            "unrealized_pnl": unrealized_pnl,
            "days_held": days_held,
            "days_remaining": days_remaining,
        })

    return result


# ── Summary stats ─────────────────────────────────────────────────────────────

def summarize_closed_trades(records: Optional[list[dict]] = None) -> dict:
    """Build aggregate performance stats for closed trades."""
    if records is None:
        records = _load_closed_trades()

    if not records:
        return {
            "total": 0,
            "target_hits": 0,
            "stop_hits": 0,
            "timeouts": 0,
            "manuals": 0,
            "win_rate_pct": 0.0,
            "average_return_pct": 0.0,
            "total_pnl": 0.0,
            "by_symbol": {},
        }

    target_hits = sum(1 for r in records if r.get("exit_reason") == "target_hit")
    stop_hits = sum(1 for r in records if r.get("exit_reason") == "stop_hit")
    timeouts = sum(1 for r in records if r.get("exit_reason") == "timeout")
    manuals = sum(1 for r in records if r.get("exit_reason") == "manual")
    win_rate = round((target_hits / len(records)) * 100.0, 2)
    avg_return = round(
        sum(float(r.get("return_pct", 0.0)) for r in records) / len(records), 2
    )
    total_pnl = round(sum(float(r.get("pnl", 0.0)) for r in records), 2)

    by_symbol: dict[str, dict] = {}
    for r in records:
        sym = (r.get("symbol") or "").upper()
        if not sym:
            continue
        b = by_symbol.setdefault(
            sym,
            {"total": 0, "target_hits": 0, "stop_hits": 0, "return_sum": 0.0},
        )
        b["total"] += 1
        if r.get("exit_reason") == "target_hit":
            b["target_hits"] += 1
        if r.get("exit_reason") == "stop_hit":
            b["stop_hits"] += 1
        b["return_sum"] += float(r.get("return_pct", 0.0))

    for sym, b in by_symbol.items():
        b["average_return_pct"] = round(b["return_sum"] / b["total"], 2) if b["total"] else 0.0
        del b["return_sum"]

    return {
        "total": len(records),
        "target_hits": target_hits,
        "stop_hits": stop_hits,
        "timeouts": timeouts,
        "manuals": manuals,
        "win_rate_pct": win_rate,
        "average_return_pct": avg_return,
        "total_pnl": total_pnl,
        "by_symbol": by_symbol,
    }
