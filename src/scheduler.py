"""Scheduler: daily auto-buy and auto-close jobs.

Uses APScheduler to run two weekday jobs:
- 9:35am ET — Scan for the top BUY stock and open a simulated 10-share position.
- 4:05pm ET — Check all open positions; close any that hit target / stop / timeout.

APScheduler is an optional dependency. If it is not installed the scheduler
silently skips setup and logs a warning — the rest of the app still works.
"""

import logging
import os
import threading
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_scheduler = None  # BackgroundScheduler instance, set by start_scheduler()
_run_guard_lock = threading.Lock()
_last_auto_buy_run_day: Optional[str] = None
_last_auto_close_run_day: Optional[str] = None
_ET = ZoneInfo("America/New_York")


def _auto_buy_quality_score(analysis) -> float:
    """Score auto-buy candidates; higher means stronger trend-quality alignment."""
    technical = getattr(analysis, "technical", None)
    fundamental = getattr(analysis, "fundamental", None)

    score = 0.0
    score += min(2.0, max(0.0, (float(getattr(analysis, "overall_score", 0.0) or 0.0) - 55.0) / 10.0))

    conviction = float(getattr(analysis, "conviction_score", 0.0) or 0.0)
    score += conviction

    trend = str(getattr(technical, "trend", "") or "").lower()
    if trend == "uptrend":
        score += 1.0

    rs_spy = getattr(technical, "relative_strength_vs_spy", None)
    if rs_spy is not None:
        score += 0.6 if float(rs_spy) > 0 else -0.6

    rsi = getattr(technical, "rsi", None)
    if rsi is not None:
        rsi_val = float(rsi)
        if 45.0 <= rsi_val <= 68.0:
            score += 0.8
        elif rsi_val > 75.0:
            score -= 1.0
        elif rsi_val < 40.0:
            score -= 0.6

    price_change_3m = getattr(technical, "price_change_3m", None)
    if price_change_3m is not None:
        pcm = float(price_change_3m)
        if 0.03 <= pcm <= 0.35:
            score += 0.6
        elif pcm > 0.50:
            score -= 0.5

    if bool(getattr(technical, "is_breakout", False)):
        score += 0.4

    eps_fwd_rev = getattr(fundamental, "eps_forward_revision", None)
    if eps_fwd_rev is not None and float(eps_fwd_rev) > 0:
        score += 0.4

    if str(getattr(analysis, "macro_regime", "") or "").lower() == "bear":
        score -= 0.8

    return round(score, 3)


def _is_auto_buy_candidate(analysis) -> bool:
    """Return True when analysis passes stricter automated entry checks."""
    if str(getattr(analysis, "recommendation", "")).upper() != "BUY":
        return False

    technical = getattr(analysis, "technical", None)
    if str(getattr(technical, "trend", "") or "").lower() != "uptrend":
        return False

    if float(getattr(analysis, "overall_score", 0.0) or 0.0) < 58.0:
        return False

    conviction = getattr(analysis, "conviction_score", None)
    if conviction is not None and float(conviction) < 0.45:
        return False

    macro_regime = getattr(analysis, "macro_regime", None)
    if isinstance(macro_regime, str) and macro_regime.lower() == "bear":
        return False

    rsi = getattr(technical, "rsi", None)
    if rsi is not None and float(rsi) > 75.0:
        return False

    rs_spy = getattr(technical, "relative_strength_vs_spy", None)
    if rs_spy is not None and float(rs_spy) < 0:
        return False

    return _auto_buy_quality_score(analysis) >= 2.2


def _today_et_key() -> str:
    return datetime.now(_ET).date().isoformat()


def _claim_daily_run(job_name: str) -> bool:
    """Return True if this job hasn't run yet today in ET, and claim today's slot."""
    global _last_auto_buy_run_day, _last_auto_close_run_day

    today = _today_et_key()
    with _run_guard_lock:
        if job_name == "auto_buy":
            if _last_auto_buy_run_day == today:
                return False
            _last_auto_buy_run_day = today
            return True
        if job_name == "auto_close":
            if _last_auto_close_run_day == today:
                return False
            _last_auto_close_run_day = today
            return True

    return False


def _maybe_run_startup_auto_buy_catchup(screener) -> None:
    """Run auto-buy once on startup if service wakes after 09:35 ET on a weekday.

    This helps free-tier deployments that sleep through the exact cron minute.
    Set SCHEDULER_STARTUP_CATCHUP_BUY=false to disable.
    """
    enabled_raw = os.getenv("SCHEDULER_STARTUP_CATCHUP_BUY", "true").strip().lower()
    if enabled_raw not in {"1", "true", "yes", "on"}:
        logger.info("startup catch-up: disabled via SCHEDULER_STARTUP_CATCHUP_BUY")
        return

    now_et = datetime.now(_ET)
    if now_et.weekday() >= 5:
        return

    current_hhmm = now_et.hour * 60 + now_et.minute
    start_hhmm = 9 * 60 + 35
    end_hhmm = 15 * 60 + 55

    if current_hhmm < start_hhmm or current_hhmm > end_hhmm:
        return

    logger.info("startup catch-up: running auto-buy once for %s ET", now_et.date().isoformat())
    auto_buy_job(screener)


def _maybe_run_startup_auto_close_catchup() -> None:
    """Run auto-close once on startup if service wakes after 16:05 ET on a weekday.

    This helps free-tier deployments that sleep through the exact cron minute.
    Set SCHEDULER_STARTUP_CATCHUP_CLOSE=false to disable.
    """
    enabled_raw = os.getenv("SCHEDULER_STARTUP_CATCHUP_CLOSE", "true").strip().lower()
    if enabled_raw not in {"1", "true", "yes", "on"}:
        logger.info("startup catch-up: auto-close disabled via SCHEDULER_STARTUP_CATCHUP_CLOSE")
        return

    now_et = datetime.now(_ET)
    if now_et.weekday() >= 5:
        return

    current_hhmm = now_et.hour * 60 + now_et.minute
    start_hhmm = 16 * 60 + 5
    end_hhmm = 23 * 60 + 55

    if current_hhmm < start_hhmm or current_hhmm > end_hhmm:
        return

    logger.info("startup catch-up: running auto-close once for %s ET", now_et.date().isoformat())
    auto_close_job()


# ── Job functions ─────────────────────────────────────────────────────────────

def auto_buy_job(screener, shares: int = 10, duration_days: int = 30, target_pct: float = 8.0) -> None:
    """Scan for top BUY recommendation and open a simulated position."""
    from src.market_universe import _get_us_market_universe
    from src.models import ScreeningFilter
    from src.recommendations import _build_exit_strategy
    from src.paper_trading import (
        assert_persistent_storage_ready_for_trading,
        has_open_position,
        open_position,
    )

    if not _claim_daily_run("auto_buy"):
        logger.info("auto_buy_job: already ran today (ET) — skipping duplicate trigger")
        return

    logger.info("auto_buy_job: starting daily scan")
    try:
        assert_persistent_storage_ready_for_trading()

        symbols = _get_us_market_universe("combined")[:80]
        filters = ScreeningFilter(min_overall_score=50)
        result = screener.screen_stocks(symbols, filters, 25, None, True)

        buys = [a for a in result.top_picks if _is_auto_buy_candidate(a)]
        if not buys:
            logger.info("auto_buy_job: no BUY recommendations today — no position opened")
            return

        buys.sort(key=_auto_buy_quality_score, reverse=True)

        top = next((a for a in buys if not has_open_position(a.symbol)), None)
        if top is None:
            logger.info("auto_buy_job: all BUY uptrend candidates already open — skipping")
            return

        exit_strategy = _build_exit_strategy(top.current_price, target_pct)

        pos = open_position(
            symbol=top.symbol,
            shares=shares,
            entry_price=top.current_price,
            target_price=exit_strategy["target_price"],
            stop_loss_price=exit_strategy["stop_loss_price"],
            duration_days=duration_days,
            target_pct=target_pct,
            recommendation_score=top.overall_score,
            source="auto",
        )
        logger.info(
            "auto_buy_job: opened %s x%d @ $%.2f (target=$%.2f stop=$%.2f)",
            pos["symbol"], pos["shares"], pos["entry_price"],
            pos["target_price"], pos["stop_loss_price"],
        )
    except Exception as e:
        logger.error("auto_buy_job failed: %s", e)


def auto_close_job() -> None:
    """Check all open positions and close any that hit target / stop / timeout."""
    from src.paper_trading import (
        assert_persistent_storage_ready_for_trading,
        check_and_close_positions,
    )

    if not _claim_daily_run("auto_close"):
        logger.info("auto_close_job: already ran today (ET) — skipping duplicate trigger")
        return

    logger.info("auto_close_job: checking open positions")
    try:
        assert_persistent_storage_ready_for_trading()

        closed = check_and_close_positions()
        if closed:
            for t in closed:
                logger.info(
                    "auto_close_job: closed %s (%s) return=%.2f%%",
                    t["symbol"], t["exit_reason"], t["return_pct"],
                )
        else:
            logger.info("auto_close_job: no positions closed")
    except Exception as e:
        logger.error("auto_close_job failed: %s", e)


# ── Scheduler lifecycle ───────────────────────────────────────────────────────

def start_scheduler(screener) -> None:
    """Start APScheduler with daily auto-buy and auto-close jobs (weekdays only).

    Jobs run in Eastern Time:
      - auto_buy  — Mon-Fri 09:35 ET  (5 min after market open)
      - auto_close — Mon-Fri 16:05 ET  (5 min after market close)
    """
    global _scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.warning(
            "APScheduler not installed — automated daily jobs disabled. "
            "Install with: pip install apscheduler"
        )
        return

    _scheduler = BackgroundScheduler(timezone="America/New_York")

    _scheduler.add_job(
        lambda: auto_buy_job(screener),
        CronTrigger(day_of_week="mon-fri", hour=9, minute=35, timezone="America/New_York"),
        id="auto_buy",
        replace_existing=True,
        misfire_grace_time=300,
    )

    _scheduler.add_job(
        auto_close_job,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=5, timezone="America/New_York"),
        id="auto_close",
        replace_existing=True,
        misfire_grace_time=300,
    )

    _scheduler.start()
    logger.info(
        "Scheduler started — auto_buy @ 09:35 ET, auto_close @ 16:05 ET (Mon-Fri)"
    )

    _maybe_run_startup_auto_buy_catchup(screener)
    _maybe_run_startup_auto_close_catchup()


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler on app exit."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
