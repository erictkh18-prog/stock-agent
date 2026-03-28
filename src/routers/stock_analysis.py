"""Router: single stock analysis (2.1).

Handles per-symbol analysis endpoints:
  GET /analyze/{symbol}       — full structured analysis (JSON model)
  GET /analyze-text/{symbol}  — human-readable one-line summary
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException

from src.models import StockAnalysis
from src.market_universe import _normalize_symbol
from src.stock_screener import StockScreener

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Stock Screening - Single Analysis"])

_screener: Optional[StockScreener] = None


def set_screener(screener: StockScreener) -> None:
    """Inject the shared StockScreener instance."""
    global _screener
    _screener = screener


def _get_screener() -> StockScreener:
    if _screener is None:
        raise RuntimeError("StockScreener not initialized in stock_analysis router")
    return _screener


@router.get("/analyze/{symbol}", response_model=StockAnalysis)
async def analyze_stock(symbol: str):
    """Analyze one stock and return a detailed structured analysis."""
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
    """Return a human-readable one-line summary for a single stock."""
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
