"""Tests for the paper trading module and its API endpoints."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import src.paper_trading as pt_module
from src.main import app
from src.models import (
    FundamentalAnalysis,
    ScreeningResult,
    SentimentAnalysis,
    StockAnalysis,
    TechnicalAnalysis,
)

client = TestClient(app, raise_server_exceptions=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_analysis(symbol: str, score: float, recommendation: str = "BUY") -> StockAnalysis:
    return StockAnalysis(
        symbol=symbol,
        name=f"{symbol} Inc.",
        current_price=150.0,
        timestamp=datetime.now(),
        fundamental=FundamentalAnalysis(score=score),
        technical=TechnicalAnalysis(score=score, trend="uptrend"),
        sentiment=SentimentAnalysis(score=60.0),
        overall_score=score,
        recommendation=recommendation,
        confidence=0.8,
        top_contributing_factors=["strong fundamentals"],
    )


def _fake_yf_history(current_price: float):
    """Return a minimal DataFrame-like object that paper_trading.py expects."""
    import pandas as pd
    return pd.DataFrame({"Close": [current_price], "High": [current_price + 1]})


# ── Unit: open_position ───────────────────────────────────────────────────────

def test_open_position_stores_record(monkeypatch, tmp_path):
    monkeypatch.setattr(pt_module, "POSITIONS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(pt_module, "CLOSED_TRADES_PATH", tmp_path / "closed_trades.json")

    pos = pt_module.open_position(
        symbol="AAPL",
        shares=10,
        entry_price=100.0,
        target_price=108.0,
        stop_loss_price=94.0,
        duration_days=30,
        target_pct=8.0,
        recommendation_score=75.0,
        source="auto",
    )

    assert pos["symbol"] == "AAPL"
    assert pos["shares"] == 10
    assert pos["entry_price"] == 100.0
    assert pos["target_price"] == 108.0
    assert pos["stop_loss_price"] == 94.0
    assert "id" in pos
    assert "opened_at" in pos
    assert "expires_at" in pos

    positions = pt_module._load_positions()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "AAPL"


# ── Unit: close_position ──────────────────────────────────────────────────────

def test_close_position_calculates_return_and_pnl(monkeypatch, tmp_path):
    monkeypatch.setattr(pt_module, "POSITIONS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(pt_module, "CLOSED_TRADES_PATH", tmp_path / "closed_trades.json")

    pos = pt_module.open_position("MSFT", 10, 200.0, 216.0, 188.0, 30, 8.0, 70.0)
    trade = pt_module.close_position(pos["id"], 216.0, "target_hit")

    assert trade is not None
    assert trade["exit_reason"] == "target_hit"
    assert trade["exit_price"] == 216.0
    assert trade["return_pct"] == 8.0
    assert trade["pnl"] == 160.0  # (216 - 200) * 10

    # Position should be removed from open list
    assert len(pt_module._load_positions()) == 0
    # Should appear in closed trades
    closed = pt_module._load_closed_trades()
    assert len(closed) == 1
    assert closed[0]["outcome"] == "target_hit"  # legacy compatibility field


def test_close_position_returns_none_for_unknown_id(monkeypatch, tmp_path):
    monkeypatch.setattr(pt_module, "POSITIONS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(pt_module, "CLOSED_TRADES_PATH", tmp_path / "closed_trades.json")

    result = pt_module.close_position("nonexistent-id", 100.0, "manual")
    assert result is None


def test_close_position_stop_hit_negative_return(monkeypatch, tmp_path):
    monkeypatch.setattr(pt_module, "POSITIONS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(pt_module, "CLOSED_TRADES_PATH", tmp_path / "closed_trades.json")

    pos = pt_module.open_position("TSLA", 5, 100.0, 108.0, 94.0, 30, 8.0, 60.0)
    trade = pt_module.close_position(pos["id"], 94.0, "stop_hit")

    assert trade["return_pct"] == -6.0
    assert trade["pnl"] == -30.0  # (94 - 100) * 5


# ── Unit: check_and_close_positions ──────────────────────────────────────────

def test_check_closes_target_hit(monkeypatch, tmp_path):
    monkeypatch.setattr(pt_module, "POSITIONS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(pt_module, "CLOSED_TRADES_PATH", tmp_path / "closed_trades.json")

    import yfinance as yf

    pt_module.open_position("NVDA", 10, 100.0, 108.0, 94.0, 30, 8.0, 80.0)

    monkeypatch.setattr(
        yf.Ticker, "history",
        lambda self, period: _fake_yf_history(110.0)  # above target
    )

    closed = pt_module.check_and_close_positions()
    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "target_hit"


def test_check_closes_stop_hit(monkeypatch, tmp_path):
    monkeypatch.setattr(pt_module, "POSITIONS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(pt_module, "CLOSED_TRADES_PATH", tmp_path / "closed_trades.json")

    import yfinance as yf

    pt_module.open_position("AMD", 10, 100.0, 108.0, 94.0, 30, 8.0, 70.0)

    monkeypatch.setattr(
        yf.Ticker, "history",
        lambda self, period: _fake_yf_history(90.0)  # below stop
    )

    closed = pt_module.check_and_close_positions()
    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "stop_hit"


def test_check_closes_expired_position(monkeypatch, tmp_path):
    monkeypatch.setattr(pt_module, "POSITIONS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(pt_module, "CLOSED_TRADES_PATH", tmp_path / "closed_trades.json")

    import yfinance as yf

    # Open a position that's already past its expiry
    pos = pt_module.open_position("GOOG", 10, 100.0, 108.0, 94.0, 30, 8.0, 65.0)
    # Backdate expires_at so the timeout triggers
    positions = pt_module._load_positions()
    positions[0]["expires_at"] = (datetime.now() - timedelta(days=1)).isoformat()
    pt_module._save_positions(positions)

    monkeypatch.setattr(
        yf.Ticker, "history",
        lambda self, period: _fake_yf_history(102.0)  # between target and stop
    )

    closed = pt_module.check_and_close_positions()
    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "timeout"


def test_check_does_not_close_active_position(monkeypatch, tmp_path):
    monkeypatch.setattr(pt_module, "POSITIONS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(pt_module, "CLOSED_TRADES_PATH", tmp_path / "closed_trades.json")

    import yfinance as yf

    pt_module.open_position("META", 10, 100.0, 108.0, 94.0, 30, 8.0, 72.0)

    monkeypatch.setattr(
        yf.Ticker, "history",
        lambda self, period: _fake_yf_history(103.0)  # between target and stop
    )

    closed = pt_module.check_and_close_positions()
    assert len(closed) == 0
    assert len(pt_module._load_positions()) == 1


def test_check_skips_position_on_fetch_error(monkeypatch, tmp_path):
    monkeypatch.setattr(pt_module, "POSITIONS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(pt_module, "CLOSED_TRADES_PATH", tmp_path / "closed_trades.json")

    import yfinance as yf
    import pandas as pd

    pt_module.open_position("XYZ", 5, 50.0, 54.0, 47.0, 30, 8.0, 55.0)

    monkeypatch.setattr(
        yf.Ticker, "history",
        lambda self, period: pd.DataFrame()  # empty → no price
    )

    closed = pt_module.check_and_close_positions()
    assert len(closed) == 0
    assert len(pt_module._load_positions()) == 1  # position stays open


# ── Unit: summarize_closed_trades ─────────────────────────────────────────────

def test_summarize_empty_records():
    result = pt_module.summarize_closed_trades([])
    assert result["total"] == 0
    assert result["win_rate_pct"] == 0.0
    assert result["total_pnl"] == 0.0


def test_summarize_calculates_win_rate_and_pnl():
    records = [
        {"symbol": "AAPL", "exit_reason": "target_hit", "return_pct": 8.0, "pnl": 80.0},
        {"symbol": "AAPL", "exit_reason": "stop_hit", "return_pct": -6.0, "pnl": -60.0},
        {"symbol": "MSFT", "exit_reason": "target_hit", "return_pct": 10.0, "pnl": 100.0},
    ]
    result = pt_module.summarize_closed_trades(records)
    assert result["total"] == 3
    assert result["target_hits"] == 2
    assert result["stop_hits"] == 1
    assert result["win_rate_pct"] == pytest.approx(66.67, abs=0.1)
    assert result["total_pnl"] == 120.0
    assert "AAPL" in result["by_symbol"]
    assert result["by_symbol"]["MSFT"]["target_hits"] == 1


# ── API: GET /paper-trading/positions ─────────────────────────────────────────

def test_api_list_positions_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(pt_module, "POSITIONS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(pt_module, "CLOSED_TRADES_PATH", tmp_path / "closed_trades.json")

    response = client.get("/paper-trading/positions")
    assert response.status_code == 200
    assert response.json()["count"] == 0


def test_api_list_positions_returns_open(monkeypatch, tmp_path):
    monkeypatch.setattr(pt_module, "POSITIONS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(pt_module, "CLOSED_TRADES_PATH", tmp_path / "closed_trades.json")

    import yfinance as yf
    monkeypatch.setattr(yf.Ticker, "history", lambda self, period: _fake_yf_history(105.0))

    pt_module.open_position("AAPL", 10, 100.0, 108.0, 94.0, 30, 8.0, 75.0)

    response = client.get("/paper-trading/positions")
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    assert data["positions"][0]["symbol"] == "AAPL"
    assert data["positions"][0]["unrealized_pct"] == 5.0


# ── API: GET /paper-trading/trades ────────────────────────────────────────────

def test_api_list_trades_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(pt_module, "POSITIONS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(pt_module, "CLOSED_TRADES_PATH", tmp_path / "closed_trades.json")

    response = client.get("/paper-trading/trades")
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 0
    assert data["summary"]["total"] == 0


def test_api_list_trades_after_close(monkeypatch, tmp_path):
    monkeypatch.setattr(pt_module, "POSITIONS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(pt_module, "CLOSED_TRADES_PATH", tmp_path / "closed_trades.json")

    pos = pt_module.open_position("NVDA", 5, 200.0, 216.0, 188.0, 30, 8.0, 80.0)
    pt_module.close_position(pos["id"], 216.0, "target_hit")

    response = client.get("/paper-trading/trades")
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    assert data["trades"][0]["exit_reason"] == "target_hit"
    assert data["summary"]["win_rate_pct"] == 100.0


# ── API: POST /paper-trading/positions/{id}/close ─────────────────────────────

def test_api_manual_close_position(monkeypatch, tmp_path):
    monkeypatch.setattr(pt_module, "POSITIONS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(pt_module, "CLOSED_TRADES_PATH", tmp_path / "closed_trades.json")

    import yfinance as yf
    monkeypatch.setattr(yf.Ticker, "history", lambda self, period: _fake_yf_history(105.0))

    pos = pt_module.open_position("AAPL", 10, 100.0, 108.0, 94.0, 30, 8.0, 75.0)

    response = client.post(f"/paper-trading/positions/{pos['id']}/close")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["trade"]["exit_reason"] == "manual"
    assert data["trade"]["return_pct"] == 5.0


def test_api_manual_close_unknown_position(monkeypatch, tmp_path):
    monkeypatch.setattr(pt_module, "POSITIONS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(pt_module, "CLOSED_TRADES_PATH", tmp_path / "closed_trades.json")

    response = client.post("/paper-trading/positions/nonexistent-id/close")
    assert response.status_code == 404


# ── API: POST /paper-trading/check-positions ─────────────────────────────────

def test_api_check_positions_closes_target(monkeypatch, tmp_path):
    monkeypatch.setattr(pt_module, "POSITIONS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(pt_module, "CLOSED_TRADES_PATH", tmp_path / "closed_trades.json")

    import yfinance as yf
    monkeypatch.setattr(yf.Ticker, "history", lambda self, period: _fake_yf_history(120.0))

    pt_module.open_position("GOOGL", 3, 100.0, 108.0, 94.0, 30, 8.0, 78.0)

    response = client.post("/paper-trading/check-positions")
    assert response.status_code == 200
    data = response.json()
    assert data["closed_count"] == 1
    assert data["closed"][0]["exit_reason"] == "target_hit"


# ── API: POST /paper-trading/auto-buy ────────────────────────────────────────

def test_api_auto_buy_opens_position(monkeypatch, tmp_path):
    import src.main as main_module

    monkeypatch.setattr(pt_module, "POSITIONS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(pt_module, "CLOSED_TRADES_PATH", tmp_path / "closed_trades.json")

    buy_analysis = _make_analysis("AAPL", 80.0, "BUY")

    def fake_screen(symbols, filters, top_n, seed=None, fast_mode=False):
        return ScreeningResult(
            total_candidates=len(symbols),
            filtered_count=1,
            top_picks=[buy_analysis],
            screening_timestamp=datetime.now(),
            deterministic_mode=False,
            seed=seed,
        )

    monkeypatch.setattr(main_module.screener, "screen_stocks", fake_screen)

    response = client.post("/paper-trading/auto-buy")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["opened_count"] >= 1
    assert data["position"]["symbol"] == "AAPL"
    assert data["position"]["shares"] == 10
    assert isinstance(data.get("opened_positions"), list)


def test_api_auto_buy_skips_existing_open_positions(monkeypatch, tmp_path):
    import src.main as main_module

    monkeypatch.setattr(pt_module, "POSITIONS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(pt_module, "CLOSED_TRADES_PATH", tmp_path / "closed_trades.json")

    # Existing open position should be skipped
    pt_module.open_position("AAPL", 10, 100.0, 108.0, 94.0, 30, 8.0, 75.0)

    buy_analysis = _make_analysis("AAPL", 80.0, "BUY")

    def fake_screen(symbols, filters, top_n, seed=None, fast_mode=False):
        return ScreeningResult(
            total_candidates=len(symbols),
            filtered_count=1,
            top_picks=[buy_analysis],
            screening_timestamp=datetime.now(),
            deterministic_mode=False,
            seed=seed,
        )

    monkeypatch.setattr(main_module.screener, "screen_stocks", fake_screen)

    response = client.post("/paper-trading/auto-buy")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "no_new_positions"
    assert "AAPL" in data.get("skipped_existing_symbols", [])


def test_api_auto_buy_opens_multiple_positions(monkeypatch, tmp_path):
    import src.main as main_module

    monkeypatch.setattr(pt_module, "POSITIONS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(pt_module, "CLOSED_TRADES_PATH", tmp_path / "closed_trades.json")

    analyses = [
        _make_analysis("AAPL", 82.0, "BUY"),
        _make_analysis("MSFT", 81.0, "BUY"),
        _make_analysis("NVDA", 80.0, "BUY"),
    ]

    def fake_screen(symbols, filters, top_n, seed=None, fast_mode=False):
        return ScreeningResult(
            total_candidates=len(symbols),
            filtered_count=len(analyses),
            top_picks=analyses,
            screening_timestamp=datetime.now(),
            deterministic_mode=False,
            seed=seed,
        )

    monkeypatch.setattr(main_module.screener, "screen_stocks", fake_screen)

    response = client.post("/paper-trading/auto-buy", params={"max_positions": 3})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["opened_count"] == 3
    symbols = {p["symbol"] for p in data.get("opened_positions", [])}
    assert symbols == {"AAPL", "MSFT", "NVDA"}


def test_api_auto_buy_returns_no_buy_when_none_qualify(monkeypatch, tmp_path):
    import src.main as main_module

    monkeypatch.setattr(pt_module, "POSITIONS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(pt_module, "CLOSED_TRADES_PATH", tmp_path / "closed_trades.json")

    hold_analysis = _make_analysis("MSFT", 55.0, "HOLD")

    def fake_screen(symbols, filters, top_n, seed=None, fast_mode=False):
        return ScreeningResult(
            total_candidates=len(symbols),
            filtered_count=1,
            top_picks=[hold_analysis],
            screening_timestamp=datetime.now(),
            deterministic_mode=False,
            seed=seed,
        )

    monkeypatch.setattr(main_module.screener, "screen_stocks", fake_screen)

    response = client.post("/paper-trading/auto-buy")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "no_buy"
    assert len(pt_module._load_positions()) == 0
