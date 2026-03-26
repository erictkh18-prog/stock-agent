"""Tests for knowledge-base ingestion URL fetching and fallback behavior."""

from unittest.mock import MagicMock, patch

import pytest
import requests
from fastapi.testclient import TestClient

from src.main import _extract_webpage_text, _github_commit_files, app


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


# ──────────────────────────────────────────────
# GitHub write-back helpers
# ──────────────────────────────────────────────


def test_github_commit_files_returns_false_without_token(monkeypatch):
    """Write-back must silently skip and return False when GITHUB_TOKEN is absent."""
    import src.main as main_module

    monkeypatch.setattr(main_module.config, "GITHUB_TOKEN", None)
    result = _github_commit_files({"knowledge-base/test.md": "# test"}, "test commit")
    assert result is False


def test_github_commit_files_commits_new_file(monkeypatch):
    """Write-back should PUT file content to GitHub Contents API when token is set."""
    import src.main as main_module

    monkeypatch.setattr(main_module.config, "GITHUB_TOKEN", "ghp_testtoken")
    monkeypatch.setattr(main_module.config, "GITHUB_REPO", "owner/repo")
    monkeypatch.setattr(main_module.config, "GITHUB_BRANCH", "main")

    get_resp = MagicMock()
    get_resp.status_code = 404  # file doesn't exist yet

    put_resp = MagicMock()
    put_resp.status_code = 201

    call_log = []

    def fake_request(method, url, **kwargs):
        call_log.append((method, url))
        if method == "GET":
            return get_resp
        return put_resp

    with patch("src.main.requests.get", side_effect=lambda url, **kw: get_resp):
        with patch("src.main.requests.put", side_effect=lambda url, **kw: put_resp):
            result = _github_commit_files({"knowledge-base/test.md": "# hello"}, "add test")

    assert result is True


def test_github_commit_files_handles_api_error_gracefully(monkeypatch):
    """A GitHub API error must not raise — it should log and return False."""
    import src.main as main_module

    monkeypatch.setattr(main_module.config, "GITHUB_TOKEN", "ghp_testtoken")
    monkeypatch.setattr(main_module.config, "GITHUB_REPO", "owner/repo")
    monkeypatch.setattr(main_module.config, "GITHUB_BRANCH", "main")

    with patch("src.main.requests.get", side_effect=requests.RequestException("network error")):
        with patch("src.main.requests.put", side_effect=requests.RequestException("network error")):
            result = _github_commit_files({"knowledge-base/test.md": "# hello"}, "fail test")

    assert result is False
