"""Tests for learning adjustments sourced from automated closed paper trades."""

import json
from datetime import datetime

from fastapi.testclient import TestClient

import src.trade_outcomes as trade_outcomes_module
import src.market_universe as market_universe_module
from src.main import app
from src.models import (
    FundamentalAnalysis,
    ScreeningResult,
    SentimentAnalysis,
    StockAnalysis,
    TechnicalAnalysis,
)


client = TestClient(app, raise_server_exceptions=True)


def _make_analysis(symbol: str, score: float, trend: str, sentiment_score: float = 50.0) -> StockAnalysis:
    return StockAnalysis(
        symbol=symbol,
        name=f"{symbol} Inc.",
        current_price=100.0,
        timestamp=datetime.now(),
        fundamental=FundamentalAnalysis(score=score),
        technical=TechnicalAnalysis(score=score, trend=trend),
        sentiment=SentimentAnalysis(score=sentiment_score),
        overall_score=score,
        recommendation="BUY" if score >= 70 else "HOLD",
        confidence=0.8,
        top_contributing_factors=["strong earnings quality and trend consistency"],
    )


def test_stock_recommendations_apply_learning_adjustment_from_closed_trades(monkeypatch, tmp_path):
    """Positive closed history should boost adjusted upside while negative history penalizes it."""
    import src.main as main_module
    import src.paper_trading as paper_trading_module

    monkeypatch.setattr(trade_outcomes_module, "TRADE_OUTCOMES_PATH", tmp_path / "trade_outcomes.json")
    monkeypatch.setattr(paper_trading_module, "POSITIONS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(paper_trading_module, "CLOSED_TRADES_PATH", tmp_path / "closed_trades.json")
    monkeypatch.setattr(market_universe_module, "_get_us_market_universe", lambda universe: ["AAPL", "MSFT"])

    closed_trades_path = tmp_path / "closed_trades.json"
    records = [
        {
            "recorded_at": datetime.now().isoformat(),
            "symbol": "AAPL",
            "outcome": "target_hit",
            "entry_price": 100,
            "exit_price": 108,
            "return_pct": 8.0,
        },
        {
            "recorded_at": datetime.now().isoformat(),
            "symbol": "AAPL",
            "outcome": "target_hit",
            "entry_price": 100,
            "exit_price": 106,
            "return_pct": 6.0,
        },
        {
            "recorded_at": datetime.now().isoformat(),
            "symbol": "MSFT",
            "outcome": "stop_hit",
            "entry_price": 100,
            "exit_price": 94,
            "return_pct": -6.0,
        },
        {
            "recorded_at": datetime.now().isoformat(),
            "symbol": "MSFT",
            "outcome": "stop_hit",
            "entry_price": 100,
            "exit_price": 95,
            "return_pct": -5.0,
        },
    ]
    closed_trades_path.write_text(json.dumps(records), encoding="utf-8")

    analyses = [
        _make_analysis("AAPL", 80.0, "uptrend", 65.0),
        _make_analysis("MSFT", 80.0, "uptrend", 65.0),
    ]

    def fake_screen_stocks(symbols, filters, top_n, seed=None, fast_mode=False):
        return ScreeningResult(
            total_candidates=len(symbols),
            filtered_count=len(analyses),
            top_picks=analyses,
            screening_timestamp=datetime.now(),
            deterministic_mode=False,
            seed=seed,
        )

    monkeypatch.setattr(main_module.screener, "screen_stocks", fake_screen_stocks)

    response = client.get(
        "/stock-recommendations",
        params={"target_percentage": 5, "duration_days": 30, "top_n": 10},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["learning"]["total_tracked_outcomes"] == 4

    results = {item["symbol"]: item for item in data["results"]}
    assert results["AAPL"]["learning_adjustment"] > 0
    assert results["MSFT"]["learning_adjustment"] < 0