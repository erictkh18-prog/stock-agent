"""Recommendations module: upside estimation, candidate building, and scan job management.

Provides helpers for constructing stock recommendation payloads, managing
background progressive scan jobs, and ranking candidates by adjusted upside.
"""

import logging
import threading
from datetime import datetime
from typing import Optional

from src.models import StockAnalysis
from src.trade_outcomes import (
    _learning_adjustment_for_symbol,
    _load_trade_outcomes,
    _summarize_trade_outcomes,
)
from src.models import ScreeningFilter
from src.stock_screener import StockScreener

logger = logging.getLogger(__name__)

# ── Scan constants ────────────────────────────────────────────────────────────

RECOMMENDATION_MIN_BASE_SCORE = 50.0
RECOMMENDATION_SCAN_TARGET_COUNT = 5
RECOMMENDATION_SCAN_BATCH_SIZE = 15

# ── Scan job state ────────────────────────────────────────────────────────────

_recommendation_jobs_lock = threading.Lock()
_recommendation_scan_jobs: dict[str, dict] = {}


# ── Upside and exit-strategy helpers ─────────────────────────────────────────

def _estimate_upside_percent(analysis: StockAnalysis, duration_days: int) -> float:
    """Estimate upside potential using analyst consensus target as the primary anchor.

    Stage 3 upgrade:
    - When an analyst mean target price is available, it becomes the primary
      anchor (discounted to 80% of consensus to account for analyst optimism
      bias) scaled by the fraction of a 12-month horizon requested.
    - Falls back to the original score-based formula when no target is present.
    """
    # --- Stage 3: Analyst consensus target (primary anchor) ---
    analyst_target = getattr(analysis, "analyst_target_price", None)
    current_price = getattr(analysis, "current_price", 0.0) or 0.0
    if analyst_target and current_price > 0 and analyst_target > current_price:
        raw_upside_pct = (analyst_target - current_price) / current_price * 100.0
        horizon_fraction = min(1.0, duration_days / 365.0)
        # 80% haircut on analyst consensus (analysts systematically over-estimate)
        projected = raw_upside_pct * 0.80 * horizon_fraction
        return round(max(1.0, min(50.0, projected)), 2)

    # --- Fallback: original score-based formula ---
    base = max(0.5, (analysis.overall_score - 48.0) * 0.30)

    trend = (getattr(analysis.technical, "trend", "") or "").lower()
    if trend == "uptrend":
        trend_bonus = 2.5
    elif trend in ("sideways", ""):
        trend_bonus = 0.5
    elif trend == "downtrend":
        trend_bonus = -1.0
    else:
        trend_bonus = 0.0

    sentiment_score = float(getattr(analysis.sentiment, "score", 50.0) or 50.0)
    sentiment_bonus = max(-2.0, min(3.0, (sentiment_score - 50.0) / 10.0))

    rsi = getattr(analysis.technical, "rsi", None)
    rsi_penalty = 1.5 if (rsi is not None and rsi > 80) else 0.0

    horizon_scale = max(0.7, min(2.0, duration_days / 30.0))
    projected = (base + trend_bonus + sentiment_bonus - rsi_penalty) * horizon_scale
    return round(max(1.0, min(50.0, projected)), 2)


def _build_simple_reason(analysis: StockAnalysis, duration_days: int, target_percentage: float) -> str:
    """Explain recommendation in non-technical language."""
    factors = analysis.top_contributing_factors or []
    trend = (getattr(analysis.technical, "trend", "") or "no clear trend").lower()
    lead_factor = factors[0] if factors else "its overall quality score is stronger than many peers"
    return (
        f"{analysis.symbol} is recommended because {lead_factor}. "
        f"Trend is currently {trend}, and the model projects a realistic chance of roughly "
        f"{target_percentage:.1f}% upside within {duration_days} days."
    )


def _build_exit_strategy(current_price: float, target_percentage: float) -> dict:
    """Build practical target and stop-loss values for risk control."""
    target_price = round(current_price * (1 + (target_percentage / 100.0)), 2)
    stop_loss_pct = max(4.0, min(12.0, target_percentage * 0.6))
    stop_loss_price = round(current_price * (1 - (stop_loss_pct / 100.0)), 2)
    return {
        "target_price": target_price,
        "stop_loss_price": stop_loss_price,
        "stop_loss_pct": round(stop_loss_pct, 2),
    }


def _build_recommendation_candidate(
    analysis: StockAnalysis,
    duration_days: int,
    target_percentage: float,
) -> dict:
    """Convert analysis into a recommendation payload for the dashboard table."""
    expected_upside = _estimate_upside_percent(analysis, duration_days)
    exit_strategy = _build_exit_strategy(analysis.current_price, target_percentage)

    conviction = round(getattr(analysis, "conviction_score", 0.0) or 0.0, 2)

    return {
        "symbol": analysis.symbol,
        "name": analysis.name,
        "current_price": round(analysis.current_price, 2),
        "overall_score": round(analysis.overall_score, 2),
        "recommendation": analysis.recommendation,
        "confidence": round(analysis.confidence, 3),
        "expected_upside_pct": expected_upside,
        "target_price": exit_strategy["target_price"],
        "stop_loss_price": exit_strategy["stop_loss_price"],
        "stop_loss_pct": exit_strategy["stop_loss_pct"],
        "conviction_score": conviction,
        "reason": _build_simple_reason(analysis, duration_days, target_percentage),
    }


def _rank_recommendation_candidates(candidates: list[dict], top_n: int) -> list[dict]:
    """Sort recommendation candidates by conviction-weighted upside then quality.

    Stage 3: Conviction score (0-1) amplifies the adjusted upside estimate by
    up to 20% so that stocks with consistent multi-factor alignment rank higher
    than single-signal outliers with similar raw upside.
    """
    for c in candidates:
        conviction = c.get("conviction_score") or 0.0
        c["conviction_weighted_upside"] = round(
            c["adjusted_upside_pct"] * (1.0 + conviction * 0.2), 2
        )
    ranked = sorted(
        candidates,
        key=lambda item: (
            item["conviction_weighted_upside"],
            item["overall_score"],
            item["confidence"],
        ),
        reverse=True,
    )
    return ranked[:top_n]


def _snapshot_recommendation_job(job: dict) -> dict:
    """Build API-safe snapshot for recommendation scan job polling."""
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "universe": job["universe"],
        "sector": job["sector"],
        "duration_days": job["duration_days"],
        "target_percentage": job["target_percentage"],
        "scanned_count": job["scanned_count"],
        "total_symbols": job["total_symbols"],
        "found_count": len(job.get("results", [])),
        "results": list(job.get("results", [])),
        "message": job.get("message", ""),
        "updated_at": job.get("updated_at", datetime.now().isoformat()),
    }


# ── Background scan worker ────────────────────────────────────────────────────

def _recommendation_scan_worker(
    job_id: str,
    screener: StockScreener,
    symbols: list[str],
    duration_days: int,
    target_percentage: float,
) -> None:
    """Background worker for progressive recommendation scanning."""
    try:
        outcome_summary = _summarize_trade_outcomes(_load_trade_outcomes())
        seen_symbols: set[str] = set()

        for start in range(0, len(symbols), RECOMMENDATION_SCAN_BATCH_SIZE):
            with _recommendation_jobs_lock:
                job = _recommendation_scan_jobs.get(job_id)
                if not job:
                    return
                if job.get("stop_requested"):
                    job["status"] = "stopped"
                    job["message"] = "Scan stopped by user."
                    return

            batch = symbols[start:start + RECOMMENDATION_SCAN_BATCH_SIZE]
            filters = ScreeningFilter(min_overall_score=RECOMMENDATION_MIN_BASE_SCORE)
            screen_result = screener.screen_stocks(
                batch,
                filters,
                top_n=len(batch),
                seed=None,
                fast_mode=True,
            )

            fresh_candidates: list[dict] = []
            for analysis in screen_result.top_picks:
                candidate = _build_recommendation_candidate(analysis, duration_days, target_percentage)
                learning_adj = _learning_adjustment_for_symbol(candidate["symbol"], outcome_summary)
                adjusted_upside = round(candidate["expected_upside_pct"] + learning_adj, 2)
                candidate["learning_adjustment"] = learning_adj
                candidate["adjusted_upside_pct"] = max(0.0, adjusted_upside)

                if learning_adj > 0:
                    candidate["reason"] += " Past tracked outcomes for this symbol have been favorable."
                elif learning_adj < 0:
                    candidate["reason"] += " Past tracked outcomes for this symbol have been weaker, so confidence is trimmed."

                if candidate["adjusted_upside_pct"] >= target_percentage:
                    symbol_key = str(candidate.get("symbol", "")).upper()
                    if symbol_key and symbol_key not in seen_symbols:
                        seen_symbols.add(symbol_key)
                        fresh_candidates.append(candidate)

            with _recommendation_jobs_lock:
                job = _recommendation_scan_jobs.get(job_id)
                if not job:
                    return
                job["scanned_count"] = min(len(symbols), start + len(batch))
                existing = list(job.get("results", []))
                existing.extend(fresh_candidates)
                job["results"] = _rank_recommendation_candidates(existing, RECOMMENDATION_SCAN_TARGET_COUNT)
                found_count = len(job["results"])
                total = job.get("total_symbols", len(symbols))
                job["message"] = (
                    f"Scanning in progress: {job['scanned_count']}/{total} symbols checked, "
                    f"{found_count} match(es) found so far."
                )
                job["updated_at"] = datetime.now().isoformat()

                if found_count >= RECOMMENDATION_SCAN_TARGET_COUNT:
                    job["status"] = "completed"
                    job["message"] = (
                        f"Scan complete: found {found_count} stocks meeting {target_percentage:.1f}% "
                        f"target within {duration_days} days."
                    )
                    job["updated_at"] = datetime.now().isoformat()
                    return

        with _recommendation_jobs_lock:
            job = _recommendation_scan_jobs.get(job_id)
            if not job:
                return
            job["status"] = "completed"
            found_count = len(job.get("results", []))
            if found_count:
                job["message"] = (
                    f"Scan finished all symbols: found {found_count} stock(s) meeting "
                    f"{target_percentage:.1f}% target within {duration_days} days."
                )
            else:
                job["message"] = (
                    "Scan finished all symbols but found no matches. "
                    "Try lower target % or longer duration."
                )
            job["updated_at"] = datetime.now().isoformat()
    except Exception as exc:
        logger.exception("Recommendation scan worker failed for job %s", job_id)
        with _recommendation_jobs_lock:
            job = _recommendation_scan_jobs.get(job_id)
            if not job:
                return
            job["status"] = "error"
            job["message"] = f"Scan failed: {exc}"
            job["updated_at"] = datetime.now().isoformat()
