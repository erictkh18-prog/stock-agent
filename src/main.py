"""Main FastAPI application for Stock Analysis Agent"""
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from typing import List, Optional
import logging
from datetime import datetime
from pathlib import Path

from src.config import config
from src.models import StockAnalysis, ScreeningFilter, ScreeningResult
from src.stock_screener import StockScreener

# Configure logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Stock Analysis Agent",
    description="AI-powered stock analysis and screening tool",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Setup static files and templates
static_dir = Path(__file__).parent.parent / "web" / "static"
templates_dir = Path(__file__).parent.parent / "web" / "templates"

if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

screener = StockScreener()

@app.get("/")
async def root():
    """Serve the dashboard"""
    dashboard_path = templates_dir / "dashboard.html"
    if dashboard_path.exists():
        return FileResponse(str(dashboard_path))
    return {"message": "Stock Analysis Agent API - Visit /docs for API documentation"}

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.now()}

@app.get("/analyze/{symbol}", response_model=StockAnalysis)
async def analyze_stock(symbol: str):
    """Analyze one stock and return detailed analysis"""
    try:
        analysis = screener.analyze_stock(symbol.upper())
        if not analysis:
            raise HTTPException(status_code=404, detail=f"Could not analyze {symbol}")
        return analysis
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error analyzing {symbol}: {e}")
        raise HTTPException(status_code=500, detail=f"Error analyzing stock: {e}")

@app.post("/screen", response_model=ScreeningResult)
async def screen_stocks(
    symbols: List[str] = Query(..., description="List of stock symbols to screen"),
    min_overall_score: Optional[float] = Query(60, ge=0, le=100),
    max_pe_ratio: Optional[float] = Query(None),
    min_dividend_yield: Optional[float] = Query(None),
    max_debt_to_equity: Optional[float] = Query(None),
    trend: Optional[str] = Query(None),
    top_n: Optional[int] = Query(10, ge=1, le=100),
):
    """Screen multiple stocks and return top candidates"""
    try:
        filters = ScreeningFilter(
            min_overall_score=min_overall_score,
            max_pe_ratio=max_pe_ratio,
            min_dividend_yield=min_dividend_yield,
            max_debt_to_equity=max_debt_to_equity,
            trend=trend,
        )
        return screener.screen_stocks(symbols, filters, top_n)
    except Exception as e:
        logger.error(f"Error screening stocks: {e}")
        raise HTTPException(status_code=500, detail=f"Error screening stocks: {e}")

@app.get("/analyze-text/{symbol}")
async def analyze_text(symbol: str):
    """Human-friendly text summary for one stock"""
    analysis = screener.analyze_stock(symbol.upper())
    if not analysis:
        raise HTTPException(status_code=404, detail=f"Could not analyze {symbol}")

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
        )
    }

@app.get("/screen-text")
async def screen_text(symbols: List[str] = Query(..., description="List of stock symbols to screen")):
    """Human-friendly text summary for multiple stocks"""
    filters = ScreeningFilter(min_overall_score=60)
    result = screener.screen_stocks(symbols, filters, top_n=len(symbols))

    return {
        "symbols": symbols,
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

@app.get("/fetch-top-performers")
async def fetch_top_performers(top_n: int = Query(10, ge=1, le=50)):
    """Analyze a curated list of popular stocks and return top picks."""
    symbols = [
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
        "META", "TSLA", "BRK-B", "JPM", "JNJ",
        "V", "PG", "UNH", "HD", "MA",
    ]

    filters = ScreeningFilter(min_overall_score=0)
    result = screener.screen_stocks(symbols, filters, top_n=top_n)

    return {
        "results": result.top_picks,
        "total_candidates": result.total_candidates,
        "filtered_count": result.filtered_count,
        "screening_timestamp": result.screening_timestamp,
    }

@app.get("/")
async def root():
    return {
        "message": "Stock Analysis Agent",
        "endpoints": {
            "health": "/health",
            "analyze": "/analyze/{symbol}",
            "screen": "/screen",
            "analyze_text": "/analyze-text/{symbol}",
            "screen_text": "/screen-text"
        },
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
