"""Tests for insider_activity module."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, call
from xml.etree import ElementTree

import pytest

from src.insider_activity import (
    _get_cik,
    _get_recent_form4_filings,
    _parse_form4_transactions,
    get_insider_signal,
)


# ── _get_cik ──────────────────────────────────────────────────────────────────

def _fake_tickers_response(symbol: str, cik: int):
    """Build a minimal company_tickers.json payload."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "0": {"cik_str": cik, "ticker": symbol, "title": "Test Co"},
    }
    return mock_resp


def test_get_cik_found(monkeypatch):
    import src.insider_activity as _ia
    # Clear CIK cache
    _ia._cik_cache.clear()
    monkeypatch.setattr("src.insider_activity.time.sleep", lambda x: None)
    monkeypatch.setattr(
        "src.insider_activity.requests.get",
        lambda *a, **kw: _fake_tickers_response("AAPL", 320193),
    )
    cik = _get_cik("AAPL")
    assert cik == "0000320193"


def test_get_cik_not_found(monkeypatch):
    import src.insider_activity as _ia
    _ia._cik_cache.clear()
    monkeypatch.setattr("src.insider_activity.time.sleep", lambda x: None)
    monkeypatch.setattr(
        "src.insider_activity.requests.get",
        lambda *a, **kw: _fake_tickers_response("AAPL", 320193),
    )
    cik = _get_cik("ZZZZ")   # Not in payload
    assert cik is None


def test_get_cik_uses_cache(monkeypatch):
    import src.insider_activity as _ia
    _ia._cik_cache.clear()
    _ia._cik_cache["MSFT"] = "0000789019"
    calls = {"n": 0}

    def fake_get(*a, **kw):
        calls["n"] += 1
        return _fake_tickers_response("MSFT", 789019)

    monkeypatch.setattr("src.insider_activity.requests.get", fake_get)
    monkeypatch.setattr("src.insider_activity.time.sleep", lambda x: None)
    result = _get_cik("MSFT")
    assert result == "0000789019"
    assert calls["n"] == 0, "Should not make HTTP call when CIK is cached"


# ── _get_recent_form4_filings ─────────────────────────────────────────────────

def _submissions_response(forms, dates, accessions, primary_docs):
    mock = MagicMock()
    mock.raise_for_status.return_value = None
    mock.json.return_value = {
        "filings": {
            "recent": {
                "form": forms,
                "filingDate": dates,
                "accessionNumber": accessions,
                "primaryDocument": primary_docs,
            }
        }
    }
    return mock


def test_get_recent_form4_filings_filters_correctly(monkeypatch):
    recent_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    old_date = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")

    monkeypatch.setattr("src.insider_activity.time.sleep", lambda x: None)
    monkeypatch.setattr(
        "src.insider_activity.requests.get",
        lambda *a, **kw: _submissions_response(
            forms=["4", "10-K", "4"],
            dates=[recent_date, recent_date, old_date],  # only first is recent Form 4
            accessions=["0001-26-000001", "0001-26-000002", "0001-26-000003"],
            primary_docs=["form4.xml", "10k.htm", "form4_old.xml"],
        ),
    )
    filings = _get_recent_form4_filings("0000320193", lookback_days=15)
    assert len(filings) == 1
    acc, doc = filings[0]
    assert acc == "000126000001"
    assert doc == "form4.xml"


def test_get_recent_form4_filings_handles_network_error(monkeypatch):
    monkeypatch.setattr("src.insider_activity.time.sleep", lambda x: None)
    monkeypatch.setattr(
        "src.insider_activity.requests.get",
        MagicMock(side_effect=ConnectionError("timeout")),
    )
    filings = _get_recent_form4_filings("0000320193", lookback_days=15)
    assert filings == []


# ── _parse_form4_transactions ─────────────────────────────────────────────────

def _xml_with_transactions(non_deriv_codes=None, deriv_codes=None):
    """Build a minimal Form 4 XML string."""
    non_deriv_codes = non_deriv_codes or []
    deriv_codes = deriv_codes or []

    nd_xml = ""
    for code in non_deriv_codes:
        nd_xml += f"""
        <nonDerivativeTransaction>
          <transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>
        </nonDerivativeTransaction>"""

    d_xml = ""
    for code in deriv_codes:
        d_xml += f"""
        <derivativeTransaction>
          <transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>
        </derivativeTransaction>"""

    return f"""<?xml version="1.0"?><ownershipDocument>
      <nonDerivativeTable>{nd_xml}</nonDerivativeTable>
      <derivativeTable>{d_xml}</derivativeTable>
    </ownershipDocument>"""


def test_parse_form4_counts_purchases(monkeypatch):
    xml = _xml_with_transactions(non_deriv_codes=["P", "P", "S"], deriv_codes=["P"])
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.content = xml.encode()
    monkeypatch.setattr("src.insider_activity.time.sleep", lambda x: None)
    monkeypatch.setattr("src.insider_activity.requests.get", lambda *a, **kw: mock_resp)

    buys, sells = _parse_form4_transactions("320193", "000132019326000001", "form4.xml")
    assert buys == 3   # 2 non-deriv P + 1 deriv P
    assert sells == 1  # 1 non-deriv S


def test_parse_form4_excludes_grants(monkeypatch):
    xml = _xml_with_transactions(non_deriv_codes=["A", "M", "P"])  # A/M should be ignored
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.content = xml.encode()
    monkeypatch.setattr("src.insider_activity.time.sleep", lambda x: None)
    monkeypatch.setattr("src.insider_activity.requests.get", lambda *a, **kw: mock_resp)

    buys, sells = _parse_form4_transactions("320193", "000132019326000001", "form4.xml")
    assert buys == 1   # only the P
    assert sells == 0


def test_parse_form4_handles_network_error(monkeypatch):
    monkeypatch.setattr("src.insider_activity.time.sleep", lambda x: None)
    monkeypatch.setattr(
        "src.insider_activity.requests.get",
        MagicMock(side_effect=ConnectionError("timeout")),
    )
    buys, sells = _parse_form4_transactions("320193", "000132019326000001", "form4.xml")
    assert buys == 0
    assert sells == 0


# ── get_insider_signal ────────────────────────────────────────────────────────

def test_get_insider_signal_buying(monkeypatch):
    import src.insider_activity as _ia
    _ia._insider_cache.clear()
    monkeypatch.setattr("src.insider_activity._get_cik", lambda s: "0000320193")
    monkeypatch.setattr(
        "src.insider_activity._get_recent_form4_filings",
        lambda cik, lookback_days: [("abcabc", "form4.xml")] * 3,
    )
    monkeypatch.setattr(
        "src.insider_activity._parse_form4_transactions",
        lambda cik, acc, doc: (2, 0),  # 2 buys, 0 sells per filing
    )
    result = get_insider_signal("AAPL", lookback_days=15)
    assert result["signal"] == "buying"
    assert result["buy_count"] == 6
    assert result["net_transactions"] == 6


def test_get_insider_signal_selling(monkeypatch):
    import src.insider_activity as _ia
    _ia._insider_cache.clear()
    monkeypatch.setattr("src.insider_activity._get_cik", lambda s: "0000320193")
    monkeypatch.setattr(
        "src.insider_activity._get_recent_form4_filings",
        lambda cik, lookback_days: [("abcabc", "form4.xml")] * 4,
    )
    monkeypatch.setattr(
        "src.insider_activity._parse_form4_transactions",
        lambda cik, acc, doc: (0, 2),  # 0 buys, 2 sells per filing
    )
    result = get_insider_signal("AAPL", lookback_days=15)
    assert result["signal"] == "selling"
    assert result["sell_count"] == 8


def test_get_insider_signal_unknown_when_no_cik(monkeypatch):
    import src.insider_activity as _ia
    _ia._insider_cache.clear()
    monkeypatch.setattr("src.insider_activity._get_cik", lambda s: None)
    result = get_insider_signal("ZZZZ", lookback_days=15)
    assert result["signal"] == "unknown"


def test_get_insider_signal_uses_cache(monkeypatch):
    import src.insider_activity as _ia
    fake_result = {"signal": "buying", "buy_count": 5, "sell_count": 0, "net_transactions": 5}
    from datetime import datetime
    _ia._insider_cache["AAPL_15"] = (datetime.now(), fake_result)
    calls = {"n": 0}

    def fake_fetch(sym, days):
        calls["n"] += 1
        return {"signal": "neutral", "buy_count": 0, "sell_count": 0, "net_transactions": 0}

    monkeypatch.setattr("src.insider_activity._fetch_insider_signal", fake_fetch)
    result = get_insider_signal("AAPL", lookback_days=15)
    assert result["signal"] == "buying"
    assert calls["n"] == 0
