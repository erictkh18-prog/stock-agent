"""Tests for knowledge-base ingestion URL fetching and fallback behavior."""

from unittest.mock import MagicMock

import pytest
import requests
from fastapi.testclient import TestClient

from src.main import _extract_webpage_text, app


client = TestClient(app, raise_server_exceptions=True)


def test_extract_webpage_text_falls_back_to_mirror_on_402(monkeypatch):
    """402 responses should retry via mirror and return extracted paragraphs."""

    blocked_response = MagicMock()
    blocked_response.status_code = 402
    blocked_response.raise_for_status = MagicMock(side_effect=requests.HTTPError("402"))

    mirror_response = MagicMock()
    mirror_response.status_code = 200
    mirror_response.text = (
        "Moving averages help smooth price action for trend identification over time.\n"
        "A crossover between short and long averages can be used as a signal.\n"
    )
    mirror_response.raise_for_status = MagicMock(return_value=None)

    def fake_get(url, headers, timeout):
        if url.startswith("https://r.jina.ai/"):
            return mirror_response
        return blocked_response

    monkeypatch.setattr("src.main.requests.get", fake_get)

    result = _extract_webpage_text("https://www.investopedia.com/terms/m/macroeconomics.asp")

    assert result["title"] == "Mirror extract"
    assert len(result["paragraphs"]) >= 1
    assert "Moving averages" in result["paragraphs"][0]


def test_extract_webpage_text_raises_for_non_retryable_errors(monkeypatch):
    """Non-retryable statuses should still raise an HTTPError."""

    bad_response = MagicMock()
    bad_response.status_code = 500
    bad_response.raise_for_status = MagicMock(side_effect=requests.HTTPError("500"))

    monkeypatch.setattr("src.main.requests.get", lambda url, headers, timeout: bad_response)

    with pytest.raises(requests.HTTPError):
        _extract_webpage_text("https://example.com/broken")


def test_extract_webpage_text_returns_placeholder_when_all_mirrors_fail(monkeypatch):
    """Retryable blocked statuses should return placeholder text if mirrors fail."""

    blocked_response = MagicMock()
    blocked_response.status_code = 402
    blocked_response.raise_for_status = MagicMock(side_effect=requests.HTTPError("402"))

    def fake_get(url, headers, timeout):
        if url.startswith("https://r.jina.ai/"):
            raise requests.RequestException("mirror unavailable")
        return blocked_response

    monkeypatch.setattr("src.main.requests.get", fake_get)

    result = _extract_webpage_text("https://www.investopedia.com/terms/m/macroeconomics.asp")

    assert result["title"] == "Blocked source (manual review required)"
    assert "Automated extraction was blocked" in result["paragraphs"][0]


def test_knowledge_base_chapter_returns_markdown_for_existing_file():
    """Knowledge-base chapter endpoint should return markdown content safely."""

    response = client.get(
        "/knowledge-base/chapter",
        params={"path": "INDEX.md"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["path"] == "INDEX.md"
    assert "Knowledge Base Index" in payload["content"]
