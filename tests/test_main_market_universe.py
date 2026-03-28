"""Tests for market-universe symbol fetching helpers in the FastAPI app."""
import json
from datetime import datetime
from unittest.mock import MagicMock

import pandas as pd
from fastapi.testclient import TestClient

from src.main import app
from src.market_universe import (
    _fetch_symbols_from_wikipedia,
    _get_us_market_universe,
    _market_universe_cache,
)
import src.market_universe as market_universe_module
import src.routers.market as market_router
from src.models import (
    ScreeningResult,
    StockAnalysis,
    FundamentalAnalysis,
    TechnicalAnalysis,
    SentimentAnalysis,
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
        top_contributing_factors=["strong earnings momentum and healthy balance sheet"],
    )


def test_fetch_symbols_from_wikipedia_uses_explicit_http_request(monkeypatch):
    """Wikipedia symbol fetch should parse HTML from an explicit requests call."""
    captured = {}

    response = MagicMock()
    response.text = "<html>mock table</html>"
    response.raise_for_status.return_value = None

    def fake_get(url, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return response

    def fake_read_html(html_stream):
        captured["html"] = html_stream.getvalue()
        return [
            pd.DataFrame({"Symbol": ["BRK.B", " msft ", None]})
        ]

    monkeypatch.setattr("src.market_universe.requests.get", fake_get)
    monkeypatch.setattr("src.market_universe.pd.read_html", fake_read_html)

    symbols = _fetch_symbols_from_wikipedia(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        ["Symbol"],
    )

    assert captured["url"].startswith("https://en.wikipedia.org/")
    assert "Mozilla/5.0" in captured["headers"]["User-Agent"]
    assert captured["timeout"] == 10
    assert captured["html"] == "<html>mock table</html>"
    assert symbols == ["BRK-B", "MSFT"]


def test_get_us_market_universe_uses_snapshot_when_wikipedia_fails(monkeypatch, tmp_path):
    """Universe builder should load disk snapshot data if Wikipedia fetching fails."""
    snapshot_path = tmp_path / "market_universe_snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "sp500": ["AAPL", "MSFT", "BRK.B"],
                "nasdaq100": ["NVDA", "AMZN"],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("src.market_universe._market_universe_snapshot_path", snapshot_path)
    monkeypatch.setattr(
        "src.market_universe._fetch_symbols_from_wikipedia",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("blocked")),
    )

    _market_universe_cache.clear()
    symbols = _get_us_market_universe("sp500")

    assert symbols[:3] == ["AAPL", "MSFT", "BRK-B"]


def test_scan_us_market_defaults_to_10_symbols(monkeypatch):
    """Default scan-us-market should only scan 10 symbols unless overridden."""
    import src.main as main_module

    market_router._market_scan_cache.clear()
    monkeypatch.setattr(market_universe_module, "_get_us_market_universe", lambda universe: [f"SYM{i}" for i in range(30)])

    captured = {"count": 0}

    def fake_screen_stocks(symbols, filters, top_n, seed=None, fast_mode=False):
        captured["count"] = len(symbols)
        return ScreeningResult(
            total_candidates=len(symbols),
            filtered_count=0,
            top_picks=[],
            screening_timestamp=datetime.now(),
            deterministic_mode=False,
            seed=seed,
        )

    monkeypatch.setattr(main_module.screener, "screen_stocks", fake_screen_stocks)

    response = client.get("/scan-us-market", params={"universe": "sp500"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["scanned_count"] == 10
    assert captured["count"] == 10


def test_stock_recommendations_returns_reason_and_exit_strategy(monkeypatch):
    """Recommendation endpoint should return simple reason plus stop-loss/target prices."""
    import src.main as main_module

    monkeypatch.setattr(market_universe_module, "_get_us_market_universe", lambda universe: ["AAPL", "MSFT"])

    analyses = [
        _make_analysis("AAPL", 88.0, "uptrend", 70.0),
        _make_analysis("MSFT", 60.0, "sideways", 50.0),
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
        params={
            "universe": "sp500",
            "max_symbols": 10,
            "top_n": 5,
            "duration_days": 30,
            "target_percentage": 8,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["recommended_count"] >= 1
    first = payload["results"][0]
    assert first["symbol"] == "AAPL"
    assert "recommended because" in first["reason"].lower()
    assert first["target_price"] > first["current_price"]
    assert first["stop_loss_price"] < first["current_price"]