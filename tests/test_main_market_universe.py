"""Tests for market-universe symbol fetching helpers in the FastAPI app."""
from unittest.mock import MagicMock

import pandas as pd

from src.main import _fetch_symbols_from_wikipedia


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

    monkeypatch.setattr("src.main.requests.get", fake_get)
    monkeypatch.setattr("src.main.pd.read_html", fake_read_html)

    symbols = _fetch_symbols_from_wikipedia(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        ["Symbol"],
    )

    assert captured["url"].startswith("https://en.wikipedia.org/")
    assert "Mozilla/5.0" in captured["headers"]["User-Agent"]
    assert captured["timeout"] == 10
    assert captured["html"] == "<html>mock table</html>"
    assert symbols == ["BRK-B", "MSFT"]