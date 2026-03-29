"""Functional tests for web UI pages and API endpoints.

Tests verify:
- HTML page responses include expected mobile-responsive markup
- Health/version/metrics endpoints return correct shapes
- Stress: multiple concurrent health checks succeed
"""
import re
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.auth import UserInfo, require_admin
from src.main import app

client = TestClient(app, raise_server_exceptions=True)

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

TEMPLATES_DIR = Path(__file__).parent.parent / "web" / "templates"


def _read_template(name: str) -> str:
    return (TEMPLATES_DIR / name).read_text(encoding="utf-8")


# ──────────────────────────────────────────────
# Health / version / metrics
# ──────────────────────────────────────────────


def test_health_returns_200():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert "paper_trading_storage" in body


def test_health_returns_503_when_persistence_enforced_and_storage_not_postgres(monkeypatch):
    monkeypatch.setenv("HEALTHCHECK_ENFORCE_TRADING_PERSISTENCE", "true")
    monkeypatch.setattr(
        "src.paper_trading.get_storage_status",
        lambda: {
            "mode": "json-local",
            "postgres_enabled": False,
            "fallback_allowed": False,
            "healthy": True,
            "message": "Using local JSON storage.",
        },
    )

    resp = client.get("/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["strict_persistence_healthcheck"] is True


def test_version_returns_version_and_commit():
    resp = client.get("/version")
    assert resp.status_code == 200
    body = resp.json()
    assert "version" in body
    assert "commit" in body
    assert "service" in body


def test_metrics_returns_200():
    resp = client.get("/metrics")
    assert resp.status_code == 200


# ──────────────────────────────────────────────
# HTML page serving
# ──────────────────────────────────────────────


def test_root_returns_html():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_stock_scanner_returns_html():
    resp = client.get("/stock-scanner")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_knowledge_base_builder_returns_html():
    resp = client.get("/knowledge-base-builder")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_admin_module_returns_html():
    resp = client.get("/admin")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Control Tower" in resp.text
    assert 'id="adminOverview"' in resp.text
    assert "Account Management" in resp.text
    assert "KB Content Management" in resp.text


def test_admin_api_docs_module_returns_html():
    resp = client.get("/admin/api-docs")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "API Documentation" in resp.text
    assert "Open Swagger API Docs" in resp.text


def test_admin_accounts_page_returns_html():
    resp = client.get("/admin/accounts")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Account Management" in resp.text
    assert "Approve / Reject Account Creation" not in resp.text  # Old text should not be here


def test_admin_kb_content_page_returns_html():
    resp = client.get("/admin/kb-content")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "KB Content Management" in resp.text
    assert "Draft Content" in resp.text
    assert "Approved Content" in resp.text
    assert "Rejected Content" in resp.text


def test_knowledge_base_viewer_returns_html():
    resp = client.get("/knowledge-base")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_knowledge_base_index_returns_tree_payload():
    resp = client.get("/knowledge-base/index")
    assert resp.status_code == 200
    body = resp.json()
    assert "sections" in body
    assert "total_topics" in body
    assert "total_chapters" in body


def test_knowledge_base_index_returns_only_approved_chapters(monkeypatch):
    monkeypatch.setattr(
        "src.routers.kb_viewer.kb._build_kb_tree",
        lambda: {
            "kb_root": "knowledge-base",
            "sections": [
                {
                    "name": "S1",
                    "topic_count": 1,
                    "topics": [
                        {
                            "name": "T1",
                            "chapter_count": 3,
                            "topic_index": "sections/s1/topics/t1/TOPIC.md",
                            "chapters": [
                                {"name": "A", "relative_path": "a.md", "status": "Approved"},
                                {"name": "D", "relative_path": "d.md", "status": "Draft"},
                                {"name": "R", "relative_path": "r.md", "status": "Rejected"},
                            ],
                        }
                    ],
                }
            ],
            "total_sections": 1,
            "total_topics": 1,
            "total_chapters": 3,
        },
    )

    resp = client.get("/knowledge-base/index")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_chapters"] == 1
    assert body["sections"][0]["topics"][0]["chapters"][0]["status"] == "Approved"


def test_knowledge_base_chapter_rejects_invalid_path_traversal():
    resp = client.get("/knowledge-base/chapter", params={"path": "../README.md"})
    assert resp.status_code == 400


def test_knowledge_base_open_explorer_rejects_invalid_path_traversal():
    app.dependency_overrides[require_admin] = lambda: UserInfo(
        email="admin@example.com", is_admin=True, is_approved=True
    )
    try:
        resp = client.post("/knowledge-base/open-explorer", json={"path": "../README.md"})
        assert resp.status_code == 400
    finally:
        app.dependency_overrides.pop(require_admin, None)


def test_knowledge_base_open_explorer_accepts_valid_path(monkeypatch):
    monkeypatch.setattr("src.knowledge_base._open_in_explorer", lambda path: None)
    app.dependency_overrides[require_admin] = lambda: UserInfo(
        email="admin@example.com", is_admin=True, is_approved=True
    )
    try:
        resp = client.post("/knowledge-base/open-explorer", json={"path": "INDEX.md"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["path"] == "INDEX.md"
    finally:
        app.dependency_overrides.pop(require_admin, None)


def test_admin_kb_chapter_details_requires_admin():
    resp = client.get("/admin/kb-chapter-details", params={"path": "INDEX.md"})
    assert resp.status_code == 401


# ──────────────────────────────────────────────
# Mobile responsiveness: viewport meta tag
# ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "template",
    [
        "dashboard.html",
        "stock-scanner.html",
        "index.html",
        "knowledge-base-builder.html",
        "knowledge-base-viewer.html",
    ],
)
def test_template_has_viewport_meta(template):
    """Every page must declare width=device-width so mobile browsers scale correctly."""
    html = _read_template(template)
    assert 'name="viewport"' in html, f"{template} missing viewport meta"
    assert "width=device-width" in html, f"{template} viewport missing width=device-width"
    assert "initial-scale=1" in html, f"{template} viewport missing initial-scale=1"


# ──────────────────────────────────────────────
# Mobile responsiveness: CSS breakpoints
# ──────────────────────────────────────────────


def test_style_css_has_mobile_breakpoints():
    css_path = Path(__file__).parent.parent / "web" / "static" / "style.css"
    css = css_path.read_text(encoding="utf-8")
    assert "@media (max-width: 768px)" in css, "style.css must have a 768px breakpoint"
    assert "@media (max-width: 480px)" in css, "style.css must have a 480px breakpoint"


def test_stock_scanner_has_mobile_breakpoints():
    html = _read_template("stock-scanner.html")
    assert "@media (max-width: 768px)" in html
    assert "@media (max-width: 480px)" in html


# ──────────────────────────────────────────────
# Mobile responsiveness: no hardcoded pixel widths on inputs
# ──────────────────────────────────────────────


def test_index_html_no_hardcoded_input_width():
    """The old index.html used style='width: 400px' which breaks on small screens."""
    html = _read_template("index.html")
    # Ensure no inline width style is set directly on an input element
    assert "width: 400px" not in html, "index.html must not contain hardcoded width:400px"
    # Check that <input> tags do not carry inline style attributes at all
    input_tags = re.findall(r'<input[^>]+>', html)
    for tag in input_tags:
        assert 'style=' not in tag, f"Input tag has inline style (use CSS classes): {tag}"


# ──────────────────────────────────────────────
# Mobile responsiveness: touch-friendly buttons (min-height in CSS)
# ──────────────────────────────────────────────


def test_style_css_has_touch_target_min_height():
    css_path = Path(__file__).parent.parent / "web" / "static" / "style.css"
    css = css_path.read_text(encoding="utf-8")
    # Ensure there is at least one min-height: 44px declaration (WCAG touch target)
    assert "min-height: 44px" in css, "style.css should define 44px min-height for touch targets"


# ──────────────────────────────────────────────
# Mobile responsiveness: no inline display:flex that ignores mobile
# ──────────────────────────────────────────────


def test_dashboard_cta_uses_cta_row_class():
    """The scanner CTA card must use .cta-row (a CSS class) instead of an inline flex style."""
    html = _read_template("dashboard.html")
    assert 'class="cta-row"' in html, "dashboard.html CTA row should use class='cta-row'"
    # Old inline style must be gone
    assert 'style="display: flex; justify-content: space-between' not in html, \
        "Inline flex style must be removed from CTA in dashboard.html"


def test_knowledge_base_viewer_has_markdown_and_filter_controls():
    html = _read_template("knowledge-base-viewer.html")
    assert 'id="treeFilter"' in html
    assert 'id="confidenceFilter"' in html
    assert 'id="chapterSort"' in html
    assert "marked.min.js" in html
    assert "dompurify" in html.lower()
    assert "Open in Explorer" in html
    assert "Open Topic Overview" in html


def test_knowledge_base_viewer_has_why_this_matters_section():
    """Viewer must include a 'Why This Matters for US Stocks' card."""
    html = _read_template("knowledge-base-viewer.html")
    assert "Why This Matters for US Stocks" in html
    assert 'id="whyMattersCard"' in html
    assert 'id="whyMattersList"' in html


def test_knowledge_base_viewer_has_key_takeaways_section():
    """Viewer must include a Key Trading Takeaways card."""
    html = _read_template("knowledge-base-viewer.html")
    assert "Key Trading Takeaways" in html
    assert 'id="takeawaysCard"' in html
    assert 'id="takeawaysList"' in html


def test_knowledge_base_viewer_has_score_bars_section():
    """Viewer must include score bars for relevance/quality visualization."""
    html = _read_template("knowledge-base-viewer.html")
    assert 'id="scoreBarsSection"' in html
    assert 'id="scoreBarsContainer"' in html
    assert "Relevance &amp; Quality Scores" in html


def test_knowledge_base_viewer_has_relevance_tier_badges():
    """Viewer must define tier-high, tier-medium, tier-low CSS classes."""
    html = _read_template("knowledge-base-viewer.html")
    assert "tier-high" in html
    assert "tier-medium" in html
    assert "tier-low" in html
    assert "relevance-tier-badge" in html


def test_knowledge_base_viewer_has_content_level_badges():
    """Viewer must define level-beginner, level-intermediate, level-advanced CSS classes."""
    html = _read_template("knowledge-base-viewer.html")
    assert "level-beginner" in html
    assert "level-intermediate" in html
    assert "level-advanced" in html
    assert "level-badge" in html


def test_knowledge_base_viewer_deprioritizes_low_quality():
    """Viewer must have CSS class for visually deprioritizing low-quality chapters."""
    html = _read_template("knowledge-base-viewer.html")
    assert "quality-low" in html


def test_knowledge_base_viewer_has_source_summary_box():
    """Viewer must display a prominently labeled source summary."""
    html = _read_template("knowledge-base-viewer.html")
    assert 'id="sourceSummaryBox"' in html
    assert "Source Summary" in html


def test_knowledge_base_chapter_api_returns_new_fields(monkeypatch):
    """Chapter API must return content_level, trading_implications, and relevance_tier."""
    import src.knowledge_base as kb

    sample_md = "\n".join([
        "---",
        "title: Test Chapter",
        "status: Approved",
        "---",
        "# Test",
        "Earnings guidance affects revenue growth momentum.",
        "## Extracted Claims",
        "- Earnings beat drives stock price momentum.",
        "## Source Summary",
        "- Earnings analysis chapter.",
    ])

    monkeypatch.setattr(kb, "_validate_kb_relative_path", lambda p: MagicMock(
        read_text=lambda encoding: sample_md,
        stem="test-chapter",
        stat=lambda: MagicMock(st_mtime=0),
    ))
    monkeypatch.setattr(kb, "_extract_chapter_status", lambda p: "Approved")
    monkeypatch.setattr(kb, "_safe_rel_path", lambda p, r: "sections/test/chapters/test-chapter.md")

    insights = kb._build_chapter_viewer_insights(sample_md, default_title="Test Chapter")

    assert "content_level" in insights
    assert insights["content_level"] in ("Beginner", "Intermediate", "Advanced")
    assert "trading_implications" in insights
    assert isinstance(insights["trading_implications"], list)
    assert len(insights["trading_implications"]) >= 1
    assert "relevance_tier" in insights
    assert insights["relevance_tier"] in ("High", "Medium", "Low")


def test_classify_content_level_beginner():
    """Simple general text should classify as Beginner."""
    from src.knowledge_base import _classify_content_level
    result = _classify_content_level("The stock market goes up and down based on supply and demand.")
    assert result == "Beginner"


def test_classify_content_level_intermediate():
    """Text with technical indicators should classify as Intermediate."""
    from src.knowledge_base import _classify_content_level
    content = (
        "Moving average crossovers with RSI confirmation provide robust trend signals. "
        "Support and resistance levels combined with volume breakout criteria improve entry quality."
    )
    result = _classify_content_level(content)
    assert result == "Intermediate"


def test_classify_content_level_advanced():
    """Text with advanced quant concepts should classify as Advanced."""
    from src.knowledge_base import _classify_content_level
    content = (
        "Statistical arbitrage strategies using cointegration and delta hedging "
        "with volatility surface calibration and pairs trading Kelly criterion sizing."
    )
    result = _classify_content_level(content)
    assert result == "Advanced"


def test_us_stock_trading_implications_returns_list():
    """_build_us_stock_trading_implications must return a non-empty list."""
    from src.knowledge_base import _build_us_stock_trading_implications
    result = _build_us_stock_trading_implications(
        topic="earnings season",
        summary="Earnings beats drive stock momentum.",
        claims=["Revenue guidance above expectations causes price acceleration."],
        markdown_content="earnings season drives momentum and volatility",
    )
    assert isinstance(result, list)
    assert len(result) >= 1


def test_us_stock_trading_implications_fallback_for_unknown_topic():
    """When no known keywords match, fallback implications must be returned."""
    from src.knowledge_base import _build_us_stock_trading_implications
    result = _build_us_stock_trading_implications(
        topic="unrelated topic xyz",
        summary="something irrelevant",
        claims=[],
        markdown_content="",
    )
    assert isinstance(result, list)
    assert len(result) >= 1


def test_knowledge_base_index_approved_chapters_include_analysis_metrics(monkeypatch):
    """Approved chapters in the tree must include weighted_relevance_score."""
    import src.routers.kb_viewer as kb_viewer_module

    monkeypatch.setattr(
        "src.routers.kb_viewer.kb._build_kb_tree",
        lambda: {
            "kb_root": "knowledge-base",
            "sections": [
                {
                    "name": "S1",
                    "topic_count": 1,
                    "topics": [
                        {
                            "name": "T1",
                            "chapter_count": 1,
                            "topic_index": None,
                            "chapters": [
                                {
                                    "name": "A",
                                    "relative_path": "a.md",
                                    "status": "Approved",
                                    "weighted_relevance_score": 82,
                                    "relevance_score": 75,
                                    "source_quality_score": 70,
                                    "confidence_band": "High",
                                },
                            ],
                        }
                    ],
                }
            ],
            "total_sections": 1,
            "total_topics": 1,
            "total_chapters": 1,
        },
    )

    resp = client.get("/knowledge-base/index")
    assert resp.status_code == 200
    body = resp.json()
    chapter = body["sections"][0]["topics"][0]["chapters"][0]
    assert chapter["weighted_relevance_score"] == 82
    assert chapter["confidence_band"] == "High"


def test_concurrent_health_checks():
    """50 concurrent /health requests must all return 200."""
    results = []
    errors = []

    def do_request():
        try:
            resp = client.get("/health")
            results.append(resp.status_code)
        except Exception as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=do_request) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"Errors during concurrent requests: {errors}"
    assert all(s == 200 for s in results), f"Not all responses were 200: {results}"


# ──────────────────────────────────────────────
# Stress test: concurrent version checks
# ──────────────────────────────────────────────


def test_concurrent_version_checks():
    """20 concurrent /version requests must succeed."""
    results = []

    def do_request():
        resp = client.get("/version")
        results.append(resp.status_code)

    threads = [threading.Thread(target=do_request) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert all(s == 200 for s in results)


# ──────────────────────────────────────────────
# Symbol validation (normalisation)
# ──────────────────────────────────────────────


def test_analyze_rejects_invalid_symbol():
    """Symbols with special chars should be rejected with 400."""
    resp = client.get("/analyze/A@PL")
    assert resp.status_code == 400


def test_analyze_rejects_too_long_symbol():
    resp = client.get("/analyze/TOOLONGSYMBOL")
    assert resp.status_code == 400


# ──────────────────────────────────────────────
# Section 2.3 / 2.4 UX differentiation
# ──────────────────────────────────────────────


def test_market_scanner_section_has_when_to_use_hint():
    """Section 2.3 must include explicit 'when to use' helper text."""
    html = _read_template("dashboard.html")
    assert 'id="market-when-to-use"' in html, "Section 2.3 must have a when-to-use hint element"
    assert "When to use" in html, "Section 2.3 must contain 'When to use' text"


def test_market_scanner_section_describes_discovery_purpose():
    """Section 2.3 description must emphasise discovery / filtering intent."""
    html = _read_template("dashboard.html")
    market_section_start = html.index('id="section-market"')
    market_section_end = html.index('id="section-recommend"')
    market_section = html[market_section_start:market_section_end]
    assert "Discovery" in market_section or "discovery" in market_section, \
        "Section 2.3 must describe its purpose as discovery/filtering"
    assert "shortlist" in market_section.lower(), \
        "Section 2.3 must mention 'shortlist' to convey broad-scan output"


def test_recommendations_section_has_when_to_use_hint():
    """Section 2.4 must include explicit 'when to use' helper text."""
    html = _read_template("dashboard.html")
    assert 'id="recommend-when-to-use"' in html, "Section 2.4 must have a when-to-use hint element"


def test_recommendations_section_describes_decision_ready_purpose():
    """Section 2.4 description must emphasise decision-ready picks with confidence/rationale."""
    html = _read_template("dashboard.html")
    recommend_section_start = html.index('id="section-recommend"')
    recommend_section = html[recommend_section_start:]
    assert "decision" in recommend_section.lower(), \
        "Section 2.4 must describe its purpose as decision-ready"
    assert "confidence" in recommend_section.lower() or "rationale" in recommend_section.lower(), \
        "Section 2.4 must mention confidence or rationale"


def test_market_scanner_result_has_transition_cta_to_recommendations():
    """Section 2.3 results area must include a prompt to move to 2.4."""
    html = _read_template("dashboard.html")
    market_result_start = html.index('id="marketScanResult"')
    # Find the closing </section> tag after the result div
    market_section_end = html.index('id="section-recommend"')
    market_result_area = html[market_result_start:market_section_end]
    assert "section-hint--cta" in market_result_area, \
        "Section 2.3 result area must include a CTA hint linking to 2.4"
    assert "section-recommend" in market_result_area, \
        "Section 2.3 CTA must link to the 2.4 Recommendations section"


# ──────────────────────────────────────────────
# Yahoo Finance symbol links in 2.3 / 2.4
# ──────────────────────────────────────────────


def test_market_scanner_js_uses_yahoo_finance_links():
    """displayScreenResults in script.js must render Yahoo Finance href for symbols."""
    js_path = Path(__file__).parent.parent / "web" / "static" / "script.js"
    js = js_path.read_text(encoding="utf-8")

    # Locate displayScreenResults function body
    func_start = js.index("function displayScreenResults(")
    func_end = js.index("\n}", func_start)
    func_body = js[func_start:func_end]

    assert "finance.yahoo.com/quote/" in func_body, \
        "displayScreenResults must build a Yahoo Finance URL for each symbol"
    assert 'target="_blank"' in func_body, \
        "displayScreenResults symbol link must open in a new tab"
    assert 'rel="noopener noreferrer"' in func_body, \
        "displayScreenResults symbol link must have rel=noopener noreferrer"


def test_recommendations_js_uses_yahoo_finance_links():
    """displayRecommendationResults in script.js must render Yahoo Finance href for symbols."""
    js_path = Path(__file__).parent.parent / "web" / "static" / "script.js"
    js = js_path.read_text(encoding="utf-8")

    func_start = js.index("function displayRecommendationResults(")
    func_end = js.index("\n}", func_start)
    func_body = js[func_start:func_end]

    assert "finance.yahoo.com/quote/" in func_body, \
        "displayRecommendationResults must build a Yahoo Finance URL for each symbol"
    assert 'target="_blank"' in func_body, \
        "displayRecommendationResults symbol link must open in a new tab"
    assert 'rel="noopener noreferrer"' in func_body, \
        "displayRecommendationResults symbol link must have rel=noopener noreferrer"


def test_symbol_links_have_accessible_label_in_js():
    """Both symbol link builders must include an aria-label for accessibility."""
    js_path = Path(__file__).parent.parent / "web" / "static" / "script.js"
    js = js_path.read_text(encoding="utf-8")

    for func_name in ("displayScreenResults", "displayRecommendationResults"):
        func_start = js.index(f"function {func_name}(")
        func_end = js.index("\n}", func_start)
        func_body = js[func_start:func_end]
        assert "aria-label" in func_body, \
            f"{func_name} symbol link must include aria-label for accessibility"
