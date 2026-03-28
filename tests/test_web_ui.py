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
    assert resp.json()["status"] == "healthy"


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


def test_knowledge_base_chapter_rejects_invalid_path_traversal():
    resp = client.get("/knowledge-base/chapter", params={"path": "../README.md"})
    assert resp.status_code == 400


def test_knowledge_base_open_explorer_rejects_invalid_path_traversal():
    resp = client.post("/knowledge-base/open-explorer", json={"path": "../README.md"})
    assert resp.status_code == 400


def test_knowledge_base_open_explorer_accepts_valid_path(monkeypatch):
    monkeypatch.setattr("src.knowledge_base._open_in_explorer", lambda path: None)
    resp = client.post("/knowledge-base/open-explorer", json={"path": "INDEX.md"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["path"] == "INDEX.md"


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
    assert "marked.min.js" in html
    assert "dompurify" in html.lower()
    assert "Open in Explorer" in html
    assert "Open Topic Overview" in html


# ──────────────────────────────────────────────
# Stress test: concurrent health checks
# ──────────────────────────────────────────────


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
