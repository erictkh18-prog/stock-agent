"""Router: multiple stock screening (2.2).

Handles batch screening and filtering endpoints:
  POST /screen          — synchronous batch screening with filters
  POST /screen-async    — async-offloaded batch screening
  GET  /screen-text     — human-readable summaries for a batch
"""

import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from src.models import ScreeningFilter, ScreeningResult
from src.market_universe import _normalize_symbols
from src.stock_screener import StockScreener

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Stock Screening - Multiple Analysis"])

_screener: Optional[StockScreener] = None


def set_screener(screener: StockScreener) -> None:
    """Inject the shared StockScreener instance."""
    global _screener
    _screener = screener


def _get_screener() -> StockScreener:
    if _screener is None:
        raise RuntimeError("StockScreener not initialized in stock_screening router")
    return _screener


def _build_screening_filter(
    min_overall_score: Optional[float],
    max_pe_ratio: Optional[float],
    max_forward_pe: Optional[float],
    min_dividend_yield: Optional[float],
    max_debt_to_equity: Optional[float],
    min_revenue_growth: Optional[float],
    min_roe: Optional[float],
    min_roa: Optional[float],
    min_profit_margin: Optional[float],
    min_operating_margin: Optional[float],
    max_peg_ratio: Optional[float],
    max_pb_ratio: Optional[float],
    max_price_to_sales: Optional[float],
    max_ev_ebitda: Optional[float],
    min_current_ratio: Optional[float],
    min_quick_ratio: Optional[float],
    min_eps: Optional[float],
    min_fcf_yield: Optional[float],
    max_beta: Optional[float],
    min_beta: Optional[float],
    min_price_change_3m: Optional[float],
    min_price_change_1m: Optional[float],
    min_volume_ratio: Optional[float],
    trend: Optional[str],
) -> ScreeningFilter:
    return ScreeningFilter(
        min_overall_score=min_overall_score,
        max_pe_ratio=max_pe_ratio,
        max_forward_pe=max_forward_pe,
        min_dividend_yield=min_dividend_yield,
        max_debt_to_equity=max_debt_to_equity,
        min_revenue_growth=min_revenue_growth,
        min_roe=min_roe,
        min_roa=min_roa,
        min_profit_margin=min_profit_margin,
        min_operating_margin=min_operating_margin,
        max_peg_ratio=max_peg_ratio,
        max_pb_ratio=max_pb_ratio,
        max_price_to_sales=max_price_to_sales,
        max_ev_ebitda=max_ev_ebitda,
        min_current_ratio=min_current_ratio,
        min_quick_ratio=min_quick_ratio,
        min_eps=min_eps,
        min_fcf_yield=min_fcf_yield,
        max_beta=max_beta,
        min_beta=min_beta,
        min_price_change_3m=min_price_change_3m,
        min_price_change_1m=min_price_change_1m,
        min_volume_ratio=min_volume_ratio,
        trend=trend,
    )


@router.post("/screen", response_model=ScreeningResult)
async def screen_stocks(
    symbols: List[str] = Query(..., description="List of stock symbols to screen"),
    min_overall_score: Optional[float] = Query(60, ge=0, le=100),
    max_pe_ratio: Optional[float] = Query(None),
    max_forward_pe: Optional[float] = Query(None),
    min_dividend_yield: Optional[float] = Query(None),
    max_debt_to_equity: Optional[float] = Query(None),
    min_revenue_growth: Optional[float] = Query(None),
    min_roe: Optional[float] = Query(None),
    min_roa: Optional[float] = Query(None),
    min_profit_margin: Optional[float] = Query(None),
    min_operating_margin: Optional[float] = Query(None),
    max_peg_ratio: Optional[float] = Query(None),
    max_pb_ratio: Optional[float] = Query(None),
    max_price_to_sales: Optional[float] = Query(None),
    max_ev_ebitda: Optional[float] = Query(None),
    min_current_ratio: Optional[float] = Query(None),
    min_quick_ratio: Optional[float] = Query(None),
    min_eps: Optional[float] = Query(None),
    min_fcf_yield: Optional[float] = Query(None),
    max_beta: Optional[float] = Query(None),
    min_beta: Optional[float] = Query(None),
    min_price_change_3m: Optional[float] = Query(None),
    min_price_change_1m: Optional[float] = Query(None),
    min_volume_ratio: Optional[float] = Query(None),
    trend: Optional[str] = Query(None),
    top_n: Optional[int] = Query(10, ge=1, le=100),
):
    """Screen multiple stocks synchronously and return top candidates."""
    try:
        normalized_symbols = _normalize_symbols(symbols)
        if not normalized_symbols:
            raise HTTPException(status_code=400, detail="No valid symbols provided")

        filters = _build_screening_filter(
            min_overall_score, max_pe_ratio, max_forward_pe, min_dividend_yield,
            max_debt_to_equity, min_revenue_growth, min_roe, min_roa, min_profit_margin,
            min_operating_margin, max_peg_ratio, max_pb_ratio, max_price_to_sales,
            max_ev_ebitda, min_current_ratio, min_quick_ratio, min_eps, min_fcf_yield,
            max_beta, min_beta, min_price_change_3m, min_price_change_1m, min_volume_ratio, trend,
        )
        return _get_screener().screen_stocks(normalized_symbols, filters, top_n)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error screening stocks: %s", exc)
        raise HTTPException(status_code=500, detail=f"Error screening stocks: {exc}")


@router.post("/screen-async", response_model=ScreeningResult)
async def screen_stocks_async(
    symbols: List[str] = Query(..., description="List of stock symbols to screen"),
    min_overall_score: Optional[float] = Query(60, ge=0, le=100),
    max_pe_ratio: Optional[float] = Query(None),
    max_forward_pe: Optional[float] = Query(None),
    min_dividend_yield: Optional[float] = Query(None),
    max_debt_to_equity: Optional[float] = Query(None),
    min_revenue_growth: Optional[float] = Query(None),
    min_roe: Optional[float] = Query(None),
    min_roa: Optional[float] = Query(None),
    min_profit_margin: Optional[float] = Query(None),
    min_operating_margin: Optional[float] = Query(None),
    max_peg_ratio: Optional[float] = Query(None),
    max_pb_ratio: Optional[float] = Query(None),
    max_price_to_sales: Optional[float] = Query(None),
    max_ev_ebitda: Optional[float] = Query(None),
    min_current_ratio: Optional[float] = Query(None),
    min_quick_ratio: Optional[float] = Query(None),
    min_eps: Optional[float] = Query(None),
    min_fcf_yield: Optional[float] = Query(None),
    max_beta: Optional[float] = Query(None),
    min_beta: Optional[float] = Query(None),
    min_price_change_3m: Optional[float] = Query(None),
    min_price_change_1m: Optional[float] = Query(None),
    min_volume_ratio: Optional[float] = Query(None),
    trend: Optional[str] = Query(None),
    top_n: Optional[int] = Query(10, ge=1, le=100),
):
    """Screen multiple stocks with async offload; avoids blocking the event loop."""
    try:
        normalized_symbols = _normalize_symbols(symbols)
        if not normalized_symbols:
            raise HTTPException(status_code=400, detail="No valid symbols provided")

        filters = _build_screening_filter(
            min_overall_score, max_pe_ratio, max_forward_pe, min_dividend_yield,
            max_debt_to_equity, min_revenue_growth, min_roe, min_roa, min_profit_margin,
            min_operating_margin, max_peg_ratio, max_pb_ratio, max_price_to_sales,
            max_ev_ebitda, min_current_ratio, min_quick_ratio, min_eps, min_fcf_yield,
            max_beta, min_beta, min_price_change_3m, min_price_change_1m, min_volume_ratio, trend,
        )
        return await asyncio.to_thread(
            _get_screener().screen_stocks,
            normalized_symbols,
            filters,
            top_n,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error screening stocks asynchronously: %s", exc)
        raise HTTPException(status_code=500, detail=f"Error screening stocks: {exc}")


@router.get("/screen-text")
async def screen_text(symbols: List[str] = Query(..., description="List of stock symbols to screen")):
    """Return human-readable text summaries for a batch of stocks."""
    normalized_symbols = _normalize_symbols(symbols)
    if not normalized_symbols:
        raise HTTPException(status_code=400, detail="No valid symbols provided")

    filters = ScreeningFilter(min_overall_score=60)
    result = _get_screener().screen_stocks(normalized_symbols, filters, top_n=len(normalized_symbols))

    return {
        "symbols": normalized_symbols,
        "results": [
            {
                "symbol": stock.symbol,
                "recommendation": stock.recommendation,
                "overall_score": stock.overall_score,
                "text": (
                    f"{stock.symbol}: {stock.recommendation} (score {stock.overall_score}). "
                    f"Fundamental {getattr(stock.fundamental, 'score', 'N/A')}, "
                    f"Technical {getattr(stock.technical, 'score', 'N/A')}, "
                    f"Sentiment {getattr(stock.sentiment, 'score', 'N/A')}."
                ),
            }
            for stock in result.top_picks
        ],
    }
