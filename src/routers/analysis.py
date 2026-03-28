"""Router: stock analysis and screening endpoints."""

import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from src.models import ScreeningFilter, ScreeningResult, StockAnalysis
from src.market_universe import _normalize_symbol, _normalize_symbols
from src.stock_screener import StockScreener

logger = logging.getLogger(__name__)

router = APIRouter()

# StockScreener is instantiated once and shared via dependency injection or
# module-level singleton.  The main app passes the shared instance in on startup.
_screener: Optional[StockScreener] = None


def set_screener(screener: StockScreener) -> None:
    """Inject the shared StockScreener instance used by all analysis endpoints."""
    global _screener
    _screener = screener


def _get_screener() -> StockScreener:
    if _screener is None:
        raise RuntimeError("StockScreener not initialized in analysis router")
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


_SCREEN_QUERY_PARAMS = dict(
    symbols=Query(..., description="List of stock symbols to screen"),
    min_overall_score=Query(60, ge=0, le=100),
    max_pe_ratio=Query(None, description="Max trailing P/E ratio"),
    max_forward_pe=Query(None, description="Max forward P/E ratio"),
    min_dividend_yield=Query(None, description="Min dividend yield (e.g. 0.02 = 2%)"),
    max_debt_to_equity=Query(None, description="Max debt-to-equity ratio"),
    min_revenue_growth=Query(None, description="Min revenue growth (e.g. 0.10 = 10%)"),
    min_roe=Query(None, description="Min return on equity (e.g. 0.15 = 15%)"),
    min_roa=Query(None, description="Min return on assets (e.g. 0.05 = 5%)"),
    min_profit_margin=Query(None, description="Min net profit margin"),
    min_operating_margin=Query(None, description="Min operating margin"),
    max_peg_ratio=Query(None, description="Max PEG ratio (e.g. 1.5)"),
    max_pb_ratio=Query(None, description="Max price-to-book ratio"),
    max_price_to_sales=Query(None, description="Max price-to-sales ratio"),
    max_ev_ebitda=Query(None, description="Max EV/EBITDA"),
    min_current_ratio=Query(None, description="Min current ratio (e.g. 1.0)"),
    min_quick_ratio=Query(None, description="Min quick ratio (e.g. 0.8)"),
    min_eps=Query(None, description="Min EPS (e.g. 0 to exclude loss-makers)"),
    min_fcf_yield=Query(None, description="Min free cash flow yield (e.g. 0.02)"),
    max_beta=Query(None, description="Max beta (e.g. 1.5 for lower volatility)"),
    min_beta=Query(None, description="Min beta (e.g. 0.5 for minimum activity)"),
    min_price_change_3m=Query(None, description="Min 3-month price change (e.g. 0.05 = 5%)"),
    min_price_change_1m=Query(None, description="Min 1-month price change"),
    min_volume_ratio=Query(None, description="Min volume ratio vs 20-day avg (e.g. 1.2)"),
    trend=Query(None, description="Price trend: uptrend, downtrend, or sideways"),
    top_n=Query(10, ge=1, le=100),
)


@router.get("/analyze/{symbol}", response_model=StockAnalysis)
async def analyze_stock(symbol: str):
    """Analyze one stock and return detailed analysis."""
    try:
        normalized_symbol = _normalize_symbol(symbol)
        if not normalized_symbol:
            raise HTTPException(status_code=400, detail="Invalid symbol format")

        analysis = _get_screener().analyze_stock(normalized_symbol)
        if not analysis:
            raise HTTPException(status_code=404, detail=f"Could not analyze {normalized_symbol}")
        return analysis
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error analyzing %s: %s", symbol, exc)
        raise HTTPException(status_code=500, detail=f"Error analyzing stock: {exc}")


@router.get("/analyze-text/{symbol}")
async def analyze_text(symbol: str):
    """Human-friendly text summary for one stock."""
    normalized_symbol = _normalize_symbol(symbol)
    if not normalized_symbol:
        raise HTTPException(status_code=400, detail="Invalid symbol format")

    analysis = _get_screener().analyze_stock(normalized_symbol)
    if not analysis:
        raise HTTPException(status_code=404, detail=f"Could not analyze {normalized_symbol}")

    fund_score = analysis.fundamental.score if analysis.fundamental else "N/A"
    tech_score = analysis.technical.score if analysis.technical else "N/A"
    sentiment_score = analysis.sentiment.score if analysis.sentiment else "N/A"

    return {
        "symbol": analysis.symbol,
        "recommendation": analysis.recommendation,
        "overall_score": analysis.overall_score,
        "summary": (
            f"{analysis.symbol}: {analysis.recommendation} (score {analysis.overall_score}). "
            f"Fundamental {fund_score}, Technical {tech_score}, Sentiment {sentiment_score}."
        ),
    }


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
    """Screen multiple stocks and return top candidates."""
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
    """Async-friendly screening endpoint that offloads CPU/network work from the event loop."""
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
    """Human-friendly text summary for multiple stocks."""
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
