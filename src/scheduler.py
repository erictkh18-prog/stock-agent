"""Scheduler: daily auto-buy and auto-close jobs.

Uses APScheduler to run two weekday jobs:
- 9:35am ET — Scan for the top BUY stock and open a simulated 10-share position.
- 4:05pm ET — Check all open positions; close any that hit target / stop / timeout.

APScheduler is an optional dependency. If it is not installed the scheduler
silently skips setup and logs a warning — the rest of the app still works.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_scheduler = None  # BackgroundScheduler instance, set by start_scheduler()


# ── Job functions ─────────────────────────────────────────────────────────────

def auto_buy_job(screener, shares: int = 10, duration_days: int = 30, target_pct: float = 8.0) -> None:
    """Scan for top BUY recommendation and open a simulated position."""
    from src.market_universe import _get_us_market_universe
    from src.models import ScreeningFilter
    from src.recommendations import _build_exit_strategy
    from src.paper_trading import (
        assert_persistent_storage_ready_for_auto_buy,
        has_open_position,
        open_position,
    )

    logger.info("auto_buy_job: starting daily scan")
    try:
        assert_persistent_storage_ready_for_auto_buy()

        symbols = _get_us_market_universe("combined")[:80]
        filters = ScreeningFilter(min_overall_score=50)
        result = screener.screen_stocks(symbols, filters, 25, None, True)

        buys = [
            a for a in result.top_picks
            if str(a.recommendation).upper() == "BUY"
            and str(getattr(a.technical, "trend", "")).lower() == "uptrend"
        ]
        if not buys:
            logger.info("auto_buy_job: no BUY recommendations today — no position opened")
            return

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
    from src.paper_trading import check_and_close_positions

    logger.info("auto_close_job: checking open positions")
    try:
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


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler on app exit."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
