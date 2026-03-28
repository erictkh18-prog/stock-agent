"""Insider activity module — SEC EDGAR Form 4 signal.

Fetches open-market purchase (P) and sale (S) transactions filed via Form 4
for a given ticker over the last N days using the SEC EDGAR REST API.
Results are cached per symbol for 24 hours (insiders don't file daily).

Open-market purchases are the strongest insider signal because:
  - Insiders buy for one reason: they believe the stock will go up.
  - Sales can be planned (10b5-1), tax-driven, or personal — less informative.

We deliberately exclude:
  - Transaction code A (awards/grants) — compensation, not conviction
  - Transaction code M (option exercises) — contractual, not voluntary
  - Transaction code G/W/I — gifts, dispositions by will, inheritances

Only P (open-market purchase) = positive signal.
Only S/D (open-market sale / disposition) = negative signal.
"""

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Optional
from xml.etree import ElementTree

import requests

logger = logging.getLogger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────────
_insider_cache: dict = {}
_insider_cache_lock = threading.Lock()
INSIDER_CACHE_TTL_SECONDS = 86400  # 24 hours

# SEC requires a descriptive User-Agent per their robots.txt
_SEC_HEADERS = {
    "User-Agent": "StockAgent/1.0 (personal-finance-tool; contact@example.com)",
    "Accept": "application/json",
}

# CIK lookup cache (company tickers rarely change)
_cik_cache: dict = {}
_cik_cache_lock = threading.Lock()

# Rate-limit: SEC asks for ≤10 requests/second
_RATE_LIMIT_DELAY = 0.12  # seconds between requests


def get_insider_signal(symbol: str, lookback_days: int = 15) -> dict:
    """Return insider transaction signal for *symbol* over the last *lookback_days*.

    Returns dict with:
        signal          : "buying" | "selling" | "neutral" | "unknown"
        buy_count       : int  (open-market purchase transactions)
        sell_count      : int  (open-market sale transactions)
        net_transactions: int  (buy_count - sell_count; positive = net buying)
    """
    now = datetime.now()
    cache_key = f"{symbol.upper()}_{lookback_days}"

    with _insider_cache_lock:
        if cache_key in _insider_cache:
            cached_at, data = _insider_cache[cache_key]
            if (now - cached_at).total_seconds() < INSIDER_CACHE_TTL_SECONDS:
                return data

    result = _fetch_insider_signal(symbol.upper(), lookback_days)

    with _insider_cache_lock:
        _insider_cache[cache_key] = (now, result)

    return result


def _fetch_insider_signal(symbol: str, lookback_days: int) -> dict:
    _unknown = {"signal": "unknown", "buy_count": 0, "sell_count": 0, "net_transactions": 0}

    cik = _get_cik(symbol)
    if not cik:
        return _unknown

    filings = _get_recent_form4_filings(cik, lookback_days)
    if not filings:
        return {"signal": "neutral", "buy_count": 0, "sell_count": 0, "net_transactions": 0}

    buy_count = 0
    sell_count = 0

    for accession, primary_doc in filings:
        b, s = _parse_form4_transactions(cik, accession, primary_doc)
        buy_count += b
        sell_count += s

    net = buy_count - sell_count

    if net >= 2:
        signal = "buying"
    elif net <= -2:
        signal = "selling"
    else:
        signal = "neutral"

    return {
        "signal": signal,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "net_transactions": net,
    }


def _get_cik(symbol: str) -> Optional[str]:
    """Resolve ticker symbol to SEC CIK (zero-padded to 10 digits)."""
    symbol_upper = symbol.upper()

    with _cik_cache_lock:
        if symbol_upper in _cik_cache:
            return _cik_cache[symbol_upper]

    try:
        time.sleep(_RATE_LIMIT_DELAY)
        resp = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=_SEC_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        tickers = resp.json()
        for _idx, entry in tickers.items():
            if entry.get("ticker", "").upper() == symbol_upper:
                cik_raw = str(entry["cik_str"])
                cik_padded = cik_raw.zfill(10)
                with _cik_cache_lock:
                    _cik_cache[symbol_upper] = cik_padded
                return cik_padded
    except Exception as exc:
        logger.debug("CIK lookup failed for %s: %s", symbol, exc)

    return None


def _get_recent_form4_filings(cik: str, lookback_days: int) -> list:
    """Return list of (accession_number, primary_document) tuples for Form 4s filed within lookback_days."""
    try:
        time.sleep(_RATE_LIMIT_DELAY)
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        resp = requests.get(url, headers=_SEC_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.debug("Submissions fetch failed for CIK %s: %s", cik, exc)
        return []

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])

    cutoff = datetime.now() - timedelta(days=lookback_days)
    result = []

    for form, date_str, acc, doc in zip(forms, dates, accessions, primary_docs):
        if form != "4":
            continue
        try:
            filed = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        if filed >= cutoff:
            result.append((acc.replace("-", ""), doc))

    return result


def _parse_form4_transactions(cik: str, accession_nodashes: str, primary_doc: str) -> tuple:
    """Fetch and parse a Form 4 XML, returning (buy_count, sell_count).

    Buy  = transaction code P (open-market purchase)
    Sell = transaction code S (open-market sale) or D (disposition)
    """
    cik_int = str(int(cik))  # Remove leading zeros for archive path
    url = (
        f"https://www.sec.gov/Archives/edgar/data/{cik_int}/"
        f"{accession_nodashes}/{primary_doc}"
    )

    try:
        time.sleep(_RATE_LIMIT_DELAY)
        resp = requests.get(url, headers={**_SEC_HEADERS, "Accept": "text/xml"}, timeout=10)
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.content)
    except Exception as exc:
        logger.debug("Form 4 fetch/parse failed (%s): %s", url, exc)
        return 0, 0

    buy_count = 0
    sell_count = 0

    # Non-derivative transactions (e.g. common stock)
    for txn in root.iter("nonDerivativeTransaction"):
        code_el = txn.find(".//transactionCode")
        if code_el is None or code_el.text is None:
            continue
        code = code_el.text.strip().upper()
        if code == "P":
            buy_count += 1
        elif code in ("S", "D"):
            sell_count += 1

    # Derivative transactions that result in acquisition/disposal of underlying
    for txn in root.iter("derivativeTransaction"):
        code_el = txn.find(".//transactionCode")
        if code_el is None or code_el.text is None:
            continue
        code = code_el.text.strip().upper()
        if code == "P":
            buy_count += 1
        elif code in ("S", "D"):
            sell_count += 1

    return buy_count, sell_count
