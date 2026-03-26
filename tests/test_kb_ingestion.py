"""Tests for knowledge-base ingestion URL fetching and fallback behavior."""

from unittest.mock import MagicMock, patch
from pathlib import Path

import pytest
import requests
from fastapi.testclient import TestClient

from src.main import (
    _discover_domain_links,
    _extract_webpage_text,
    _github_commit_files,
    _is_claim_noise,
    _is_low_quality_source_extract,
    _rank_and_deduplicate_claims,
    _source_domain_from_url,
    app,
)


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


def test_discover_domain_links_extracts_direct_urls_from_ddg_redirect(monkeypatch):
    """Domain discovery should unwrap DuckDuckGo redirect URLs."""
    import src.main as main_module

    html = """
    <html><body>
      <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.reuters.com%2Fmarkets%2Fus%2Ffed-rates">Fed Story</a>
      <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.bloomberg.com%2Fnews%2Farticles%2Fabc">Bloomberg Story</a>
    </body></html>
    """

    response = MagicMock()
    response.raise_for_status = MagicMock(return_value=None)
    response.text = html

    monkeypatch.setattr(main_module.requests, "get", lambda *args, **kwargs: response)

    links = _discover_domain_links("rates", "reuters.com", max_links=2)

    assert links
    assert links[0].startswith("https://www.reuters.com/")


def test_knowledge_base_ingest_without_url_runs_auto_research(monkeypatch, tmp_path):
    """Topic-only ingestion should auto-research and create a trading-focused draft chapter."""
    import src.main as main_module

    monkeypatch.setattr(main_module, "KB_ROOT", tmp_path)
    monkeypatch.setattr(main_module, "KB_CHANGELOG_PATH", tmp_path / "CHANGELOG.md")
    monkeypatch.setattr(main_module, "_github_commit_files", lambda files, message: True)

    auto_result = {
        "source_title": "Auto research synthesis from Reuters, Bloomberg, and Investopedia",
        "summary": "Inflation and rates regime continue to shape equity risk appetite.",
        "claims": [
            "Inflation data is shaping risk sentiment and cross-asset positioning.",
            "Interest rate expectations remain the key driver for valuation expansion.",
        ],
        "trading_insights": [
            "Inflation data is shaping risk sentiment and cross-asset positioning. -> Use CPI events as volatility windows with tighter risk controls.",
            "Interest rate expectations remain the key driver for valuation expansion. -> Rotate toward rate-sensitive sectors only after trend confirmation.",
        ],
        "source_urls": [
            "https://www.reuters.com/site-search/?query=inflation",
            "https://www.bloomberg.com/search?query=inflation",
            "https://www.investopedia.com/search?q=inflation",
        ],
        "source_labels": [],
    }

    monkeypatch.setattr(main_module, "_auto_research_topic", lambda topic: auto_result)

    response = client.post(
        "/knowledge-base/ingest",
        json={"topic": "Macro Economic", "url": ""},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["url"] == "AUTO_DISCOVERY"
    assert payload["source_title"].startswith("Auto research synthesis")

    chapter_path = Path(payload["created_chapter"])
    assert chapter_path.exists()
    chapter_content = chapter_path.read_text(encoding="utf-8")
    assert "# Trading Application Notes" in chapter_content
    assert "# Source Acquisition Mode" in chapter_content
    assert "Auto multi-source topic research" in chapter_content


def test_rank_and_deduplicate_claims_prioritizes_relevant_finance_content():
    """Ranking should keep relevant macro/finance claims and drop noisy duplicates."""
    claims = [
        {
            "text": "Inflation data and bond yields continue to drive equity valuation shifts across sectors.",
            "source_domain": "reuters.com",
        },
        {
            "text": "Inflation data and bond yields continue to drive equity valuation shifts across sectors!",
            "source_domain": "bloomberg.com",
        },
        {
            "text": "Click here to subscribe for unlimited content and cookie updates.",
            "source_domain": "investopedia.com",
        },
        {
            "text": "The Federal Reserve policy outlook changed interest-rate expectations for growth stocks.",
            "source_domain": "reuters.com",
        },
    ]

    selected = _rank_and_deduplicate_claims(claims, "macro inflation trading")

    assert len(selected) == 2
    assert any("Inflation data and bond yields" in claim for claim in selected)
    assert any("Federal Reserve policy outlook" in claim for claim in selected)


def test_rank_and_deduplicate_claims_balances_sources_when_available():
    """Selection should include at least one claim per available source when quality permits."""
    claims = [
        {
            "text": "Reuters notes inflation pressure and bond-yield repricing across cyclicals.",
            "source_domain": "reuters.com",
        },
        {
            "text": "Bloomberg reports central-bank messaging shifted expectations for future rates.",
            "source_domain": "bloomberg.com",
        },
        {
            "text": "Investopedia explains how interest-rate policy changes affect sector rotation and valuation assumptions.",
            "source_domain": "investopedia.com",
        },
    ]

    selected = _rank_and_deduplicate_claims(claims, "inflation interest rates")

    assert len(selected) == 3
    assert any("Reuters notes inflation" in claim for claim in selected)
    assert any("Bloomberg reports central-bank" in claim for claim in selected)
    assert any("Investopedia explains" in claim for claim in selected)


def test_is_low_quality_source_extract_flags_blocked_placeholder():
    """Blocked placeholder extracts should be excluded from synthesis."""
    assert _is_low_quality_source_extract(
        "Blocked source (manual review required)",
        ["Automated extraction was blocked by the source (HTTP 402)."],
    ) is True

    assert _is_low_quality_source_extract(
        "Reuters Markets",
        ["Inflation and rates expectations continue to impact risk assets globally."],
    ) is False


def test_source_domain_from_url_matches_known_domains():
    """Domain mapper should normalize URLs to known source labels."""
    assert _source_domain_from_url("https://www.reuters.com/markets/us/fed") == "reuters.com"
    assert _source_domain_from_url("https://www.bloomberg.com/news/articles/x") == "bloomberg.com"
    assert _source_domain_from_url("https://www.investopedia.com/terms/i/inflation.asp") == "investopedia.com"


def test_is_claim_noise_blocks_navigation_fragments():
    """Navigation and account/marketing snippets should be rejected as claims."""
    assert _is_claim_noise("[Skip to content](https://example.com#content)") is True
    assert _is_claim_noise("Bloomberg the Company & Its Products and customer support links") is True
    assert _is_claim_noise("Interest-rate expectations are repricing growth-equity valuation multiples.") is False


def test_chapter_status_endpoint_updates_frontmatter(monkeypatch, tmp_path):
    """Approve/reject endpoint should update status and append review note."""
    import src.main as main_module

    monkeypatch.setattr(main_module, "KB_ROOT", tmp_path)
    monkeypatch.setattr(main_module, "KB_CHANGELOG_PATH", tmp_path / "CHANGELOG.md")
    monkeypatch.setattr(main_module, "_github_commit_files", lambda files, message: True)

    chapter_rel = "sections/02-trading-domain/topics/auto-test/chapters/ch1.md"
    chapter_path = tmp_path / chapter_rel
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path.write_text(
        "\n".join(
            [
                "---",
                "chapter_id: CH-AUTO-TEST",
                "title: test topic",
                "status: Draft",
                "owner: Eric + Copilot",
                "last_reviewed: 2026-03-26",
                "confidence: Medium",
                "sources:",
                "  - https://www.reuters.com/",
                "---",
                "",
                "# Objective",
                "- Test",
                "",
            ]
        ),
        encoding="utf-8",
    )

    response = client.post(
        "/knowledge-base/chapter-status",
        json={
            "path": chapter_rel,
            "status": "Approved",
            "note": "Reviewed and accepted",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["chapter_status"] == "Approved"

    updated = chapter_path.read_text(encoding="utf-8")
    assert "status: Approved" in updated
    assert "# Review Decision" in updated
    assert "Reviewed and accepted" in updated
