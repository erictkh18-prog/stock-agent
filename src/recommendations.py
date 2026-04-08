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
RECOMMENDATION_SCAN_TARGET_COUNT = 10
RECOMMENDATION_SCAN_BATCH_SIZE = 20

# ── Scan job state ────────────────────────────────────────────────────────────

_recommendation_jobs_lock = threading.Lock()
_recommendation_scan_jobs: dict[str, dict] = {}


# ── Upside and exit-strategy helpers ─────────────────────────────────────────

def _estimate_upside_percent(analysis: StockAnalysis, duration_days: Optional[int]) -> float:
    """Estimate upside potential using analyst consensus target as the primary anchor.

    Stage 3 upgrade:
    - When an analyst mean target price is available, it becomes the primary
      anchor (discounted to 80% of consensus to account for analyst optimism
      bias) scaled by the fraction of a 12-month horizon requested.
    - Falls back to the original score-based formula when no target is present.
    """
    # --- Stage 3: Analyst consensus target (primary anchor) ---
    effective_duration_days = duration_days if duration_days is not None else 365

    analyst_target = getattr(analysis, "analyst_target_price", None)
    current_price = getattr(analysis, "current_price", 0.0) or 0.0
    if analyst_target and current_price > 0 and analyst_target > current_price:
        raw_upside_pct = (analyst_target - current_price) / current_price * 100.0
        horizon_fraction = min(1.0, effective_duration_days / 365.0)
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

    horizon_scale = max(0.7, min(2.0, effective_duration_days / 30.0))
    projected = (base + trend_bonus + sentiment_bonus - rsi_penalty) * horizon_scale
    return round(max(1.0, min(50.0, projected)), 2)


def _build_simple_reason(
    analysis: StockAnalysis,
    duration_days: Optional[int],
    target_percentage: Optional[float],
) -> str:
    """Explain recommendation in non-technical language."""
    factors = analysis.top_contributing_factors or []
    trend = (getattr(analysis.technical, "trend", "") or "no clear trend").lower()
    lead_factor = factors[0] if factors else "its overall quality score is stronger than many peers"
    suffix = ""
    if target_percentage is not None and duration_days is not None:
        suffix = (
            f" The model projects a realistic chance of roughly {target_percentage:.1f}% "
            f"upside within {duration_days} days."
        )
    elif target_percentage is not None:
        suffix = f" The model projects roughly {target_percentage:.1f}% upside with no fixed time window."
    elif duration_days is not None:
        suffix = f" The model projects upside over about {duration_days} days."
    else:
        suffix = " Target is price-based and not tied to a fixed duration."

    return f"{analysis.symbol} is recommended because {lead_factor}. Trend is currently {trend}.{suffix}"


def _build_technical_reason(analysis: StockAnalysis, target_price: float) -> str:
    """Build concise technical explanation for recommendation rows."""
    trend = (getattr(analysis.technical, "trend", None) or "unknown").lower()
    rsi = getattr(analysis.technical, "rsi", None)
    rs_spy = getattr(analysis.technical, "relative_strength_vs_spy", None)
    score = round(float(getattr(analysis, "overall_score", 0.0) or 0.0), 1)
    confidence = round(float(getattr(analysis, "confidence", 0.0) or 0.0) * 100.0, 1)

    parts = [f"Trend={trend}", f"score={score}", f"confidence={confidence}%", f"target=${target_price:.2f}"]
    if rsi is not None:
        parts.append(f"RSI={float(rsi):.1f}")
    if rs_spy is not None:
        parts.append(f"RS vs SPY={float(rs_spy):.2f}%")
    return ", ".join(parts)


def _build_layman_reason(analysis: StockAnalysis, target_price: float, duration_days: Optional[int]) -> str:
    """Build plain-language explanation focused on decision usefulness."""
    symbol = analysis.symbol
    trend = (getattr(analysis.technical, "trend", None) or "unclear").lower()
    confidence = round(float(getattr(analysis, "confidence", 0.0) or 0.0) * 100.0, 1)
    score = round(float(getattr(analysis, "overall_score", 0.0) or 0.0), 1)
    current_price = float(getattr(analysis, "current_price", 0.0) or 0.0)

    rsi = getattr(analysis.technical, "rsi", None)
    momentum_3m = getattr(analysis.technical, "price_change_3m", None)
    rs_spy = getattr(analysis.technical, "relative_strength_vs_spy", None)
    sentiment_score = getattr(analysis.sentiment, "score", None)
    eps_forward_revision = getattr(analysis.fundamental, "eps_forward_revision", None)
    roic = getattr(analysis.fundamental, "roic", None)
    breakout = getattr(analysis.technical, "is_breakout", None)

    signal_phrases = []
    if breakout is True:
        signal_phrases.append("price is breaking out with strong participation")
    if rs_spy is not None and rs_spy > 0:
        signal_phrases.append("it is outperforming the broader market")
    if momentum_3m is not None and momentum_3m > 0:
        signal_phrases.append(f"recent 3-month momentum is positive ({momentum_3m:.1f}%)")
    if rsi is not None and 40 <= rsi <= 70:
        signal_phrases.append(f"RSI is in a healthier trend zone ({rsi:.1f})")
    if eps_forward_revision is not None and eps_forward_revision > 0:
        signal_phrases.append("analysts have been revising earnings expectations upward")
    if roic is not None and roic >= 0.12:
        signal_phrases.append("capital efficiency is strong")
    if sentiment_score is not None and sentiment_score >= 60:
        signal_phrases.append("sentiment is supportive")

    if duration_days is not None:
        window_text = f"The preferred window is {duration_days} days"
    else:
        window_text = "There is no fixed deadline"

    lead_factor = (analysis.top_contributing_factors or ["its trend and quality signals are aligned"])[0]
    primary_signal = signal_phrases[0] if signal_phrases else lead_factor
    secondary_signal = signal_phrases[1] if len(signal_phrases) > 1 else None

    target_delta = (target_price - current_price) if current_price > 0 else 0.0
    target_pct = ((target_delta / current_price) * 100.0) if current_price > 0 else 0.0

    secondary_text = f" Secondary support: {secondary_signal}." if secondary_signal else ""
    return (
        f"{symbol} is in an {trend} and rated BUY with score {score} and confidence {confidence}%. "
        f"Main support for this setup: {primary_signal}.{secondary_text} "
        f"From ${current_price:.2f}, the target is ${target_price:.2f} (~{target_pct:.1f}% upside); "
        f"{window_text}, and risk is managed with the stop-loss shown."
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
    duration_days: Optional[int],
    target_percentage: Optional[float],
) -> dict:
    """Convert analysis into a recommendation payload for the dashboard table."""
    expected_upside = _estimate_upside_percent(analysis, duration_days)
    target_pct_for_exit = float(target_percentage) if target_percentage is not None else max(5.0, min(20.0, expected_upside))
    exit_strategy = _build_exit_strategy(analysis.current_price, target_pct_for_exit)

    conviction = round(getattr(analysis, "conviction_score", 0.0) or 0.0, 2)
    layman_reason = _build_layman_reason(analysis, exit_strategy["target_price"], duration_days)
    technical_reason = _build_technical_reason(analysis, exit_strategy["target_price"])

    return {
        "symbol": analysis.symbol,
        "name": analysis.name,
        "current_price": round(analysis.current_price, 2),
        "overall_score": round(analysis.overall_score, 2),
        "recommendation": analysis.recommendation,
        "trend": getattr(analysis.technical, "trend", None),
        "confidence": round(analysis.confidence, 3),
        "expected_upside_pct": expected_upside,
        "target_pct_used": round(target_pct_for_exit, 2),
        "target_price": exit_strategy["target_price"],
        "target_duration_days": duration_days,
        "stop_loss_price": exit_strategy["stop_loss_price"],
        "stop_loss_pct": exit_strategy["stop_loss_pct"],
        "conviction_score": conviction,
        "reason": _build_simple_reason(analysis, duration_days, target_percentage),
        "technical_reason": technical_reason,
        "layman_reason": layman_reason,
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
        "top_n": job.get("top_n"),
        "max_symbols": job.get("max_symbols"),
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
    duration_days: Optional[int],
    target_percentage: Optional[float],
    target_count: int,
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

                is_buy_uptrend = (
                    str(candidate.get("recommendation", "")).upper() == "BUY"
                    and str(candidate.get("trend", "")).lower() == "uptrend"
                )
                passes_target = (
                    True if target_percentage is None
                    else candidate["adjusted_upside_pct"] >= target_percentage
                )

                if is_buy_uptrend and passes_target:
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
                job["results"] = _rank_recommendation_candidates(existing, target_count)
                found_count = len(job["results"])
                total = job.get("total_symbols", len(symbols))
                job["message"] = (
                    f"Scanning in progress: {job['scanned_count']}/{total} symbols checked, "
                    f"{found_count} match(es) found so far."
                )
                job["updated_at"] = datetime.now().isoformat()

                if found_count >= target_count:
                    job["status"] = "completed"
                    if target_percentage is not None and duration_days is not None:
                        job["message"] = (
                            f"Scan complete: found {found_count} BUY uptrend stocks meeting {target_percentage:.1f}% "
                            f"target within {duration_days} days."
                        )
                    elif target_percentage is not None:
                        job["message"] = (
                            f"Scan complete: found {found_count} BUY uptrend stocks meeting {target_percentage:.1f}% target."
                        )
                    else:
                        job["message"] = f"Scan complete: found {found_count} BUY uptrend stocks."
                    job["updated_at"] = datetime.now().isoformat()
                    return

        with _recommendation_jobs_lock:
            job = _recommendation_scan_jobs.get(job_id)
            if not job:
                return
            job["status"] = "completed"
            found_count = len(job.get("results", []))
            if found_count:
                if target_percentage is not None and duration_days is not None:
                    job["message"] = (
                        f"Scan finished all symbols: found {found_count} BUY uptrend stock(s) meeting "
                        f"{target_percentage:.1f}% target within {duration_days} days."
                    )
                elif target_percentage is not None:
                    job["message"] = (
                        f"Scan finished all symbols: found {found_count} BUY uptrend stock(s) meeting "
                        f"{target_percentage:.1f}% target."
                    )
                else:
                    job["message"] = (
                        f"Scan finished all symbols: found {found_count} BUY uptrend stock(s)."
                    )
            else:
                job["message"] = "Scan finished all symbols but found no BUY uptrend matches."
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
