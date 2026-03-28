"""Router: Knowledge Base API endpoints.

Page-serving endpoints (/knowledge-base, /knowledge-base-builder) are defined in
main.py so they can easily access the shared templates_dir path.
"""

import asyncio
import logging
import threading
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

import src.knowledge_base as kb
from src.auth import UserInfo, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()


# ── API endpoints ─────────────────────────────────────────────────────────────

@router.get("/knowledge-base/index")
async def knowledge_base_index():
    """Return section/topic/chapter tree for knowledge-base browsing."""
    if not kb.KB_ROOT.exists():
        raise HTTPException(status_code=404, detail="Knowledge base root not found")
    return kb._build_kb_tree()


@router.get("/knowledge-base/chapter")
async def knowledge_base_chapter(
    path: str = Query(..., description="Knowledge-base relative markdown path"),
):
    """Return chapter markdown content and metadata for viewer rendering."""
    if not path.strip():
        raise HTTPException(status_code=400, detail="Chapter path is required")

    chapter_path = kb._validate_kb_relative_path(path.strip())
    content = chapter_path.read_text(encoding="utf-8")

    return {
        "path": kb._safe_rel_path(chapter_path, kb.KB_ROOT),
        "title": chapter_path.stem,
        "updated_at": datetime.fromtimestamp(chapter_path.stat().st_mtime).isoformat(),
        "content": content,
    }


@router.post("/knowledge-base/open-explorer", response_model=kb.KnowledgeOpenExplorerResponse)
async def knowledge_base_open_explorer(payload: kb.KnowledgeOpenExplorerRequest):
    """Open chapter file location in local file explorer for quick editing."""
    relative_path = payload.path.strip()
    if not relative_path:
        raise HTTPException(status_code=400, detail="Chapter path is required")

    chapter_path = kb._validate_kb_relative_path(relative_path)

    try:
        await asyncio.to_thread(kb._open_in_explorer, chapter_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not open explorer: {exc}") from exc

    return kb.KnowledgeOpenExplorerResponse(
        status="ok",
        path=kb._safe_rel_path(chapter_path, kb.KB_ROOT),
        message="Explorer opened for chapter path",
    )


@router.post("/knowledge-base/chapter-status", response_model=kb.KnowledgeChapterStatusResponse)
async def knowledge_base_chapter_status(
    payload: kb.KnowledgeChapterStatusRequest,
    current_user: UserInfo = Depends(get_current_user),
):
    """Approve/reject/draft a chapter by updating frontmatter status.

    Setting status to Approved or Rejected is restricted to admins.
    Any authenticated contributor may revert a chapter to Draft.
    """
    relative_path = payload.path.strip()
    requested_status = payload.status.strip().title()
    note = payload.note.strip() if payload.note else ""

    if not relative_path:
        raise HTTPException(status_code=400, detail="Chapter path is required")

    allowed_statuses = {"Approved", "Rejected", "Draft"}
    if requested_status not in allowed_statuses:
        raise HTTPException(status_code=400, detail="Status must be one of: Approved, Rejected, Draft")

    if requested_status in {"Approved", "Rejected"} and not current_user.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Only the admin can approve or reject content",
        )

    chapter_path = kb._validate_kb_relative_path(relative_path)
    await asyncio.to_thread(kb._apply_chapter_status_update, chapter_path, requested_status, note)

    rel_chapter = kb._safe_rel_path(chapter_path, kb.KB_ROOT)
    kb._append_kb_changelog(f"Chapter status set to {requested_status}: {chapter_path.as_posix()}")

    chapter_repo_path = "knowledge-base/" + rel_chapter
    changelog_repo_path = "knowledge-base/" + kb._safe_rel_path(kb.KB_CHANGELOG_PATH, kb.KB_ROOT)

    def _do_github_status_writeback() -> None:
        files_to_commit = {
            chapter_repo_path: chapter_path.read_text(encoding="utf-8"),
            changelog_repo_path: kb.KB_CHANGELOG_PATH.read_text(encoding="utf-8"),
        }
        kb._github_commit_files(
            files_to_commit,
            f"kb: set chapter status '{requested_status}' for {rel_chapter}",
        )

    threading.Thread(target=_do_github_status_writeback, daemon=True).start()

    return kb.KnowledgeChapterStatusResponse(
        status="ok",
        path=rel_chapter,
        chapter_status=requested_status,
        message="Chapter status updated",
    )


@router.post("/knowledge-base/ingest", response_model=kb.KnowledgeIngestResponse)
async def knowledge_base_ingest(
    payload: kb.KnowledgeIngestRequest,
    current_user: UserInfo = Depends(get_current_user),
):
    """Ingest website content into section/topic/chapter structure.

    Requires a valid authenticated session (Bearer token).
    """
    import requests as _requests

    topic = payload.topic.strip()
    source_url = (payload.url or "").strip()
    if not topic:
        raise HTTPException(status_code=400, detail="Topic is required")

    source_urls: list[str] = []
    source_title = ""
    summary = ""
    claims: list[str] = []
    trading_insights: list[str] = []

    if source_url:
        kb._validate_ingestion_url(source_url)
        try:
            extracted = await asyncio.to_thread(kb._extract_webpage_text, source_url)
        except _requests.RequestException as exc:
            raise HTTPException(status_code=400, detail=f"Could not fetch URL: {exc}") from exc

        paragraphs = extracted.get("paragraphs", [])
        summary = paragraphs[0] if paragraphs else "No substantial text extracted from source."
        claims = paragraphs[:5]
        trading_insights = kb._derive_trading_application_insights(topic, claims)
        source_title = extracted.get("title", "Untitled source")
        source_urls = [source_url]
    else:
        auto_result = await asyncio.to_thread(kb._auto_research_topic, topic)
        source_title = auto_result["source_title"]
        summary = auto_result["summary"]
        claims = auto_result["claims"]
        trading_insights = auto_result["trading_insights"]
        source_urls = auto_result["source_urls"]

        if not source_urls:
            source_urls = [
                kb._fallback_domain_search_url(topic, "reuters.com"),
                kb._fallback_domain_search_url(topic, "bloomberg.com"),
                kb._fallback_domain_search_url(topic, "investopedia.com"),
            ]

    topic_dir, chapters_dir = kb._build_kb_topic_paths(topic)
    topic_index = kb._write_topic_index_if_missing(topic_dir, topic)

    timestamp = datetime.now()
    chapter_name = f"{timestamp.strftime('%Y%m%d-%H%M%S')}-{kb._slugify(topic)}.md"
    chapter_path = chapters_dir / chapter_name

    chapter_lines = [
        "---",
        f"chapter_id: CH-AUTO-{timestamp.strftime('%Y%m%d%H%M%S')}",
        f"title: {topic}",
        "status: Draft",
        "owner: Eric + Copilot",
        f"last_reviewed: {timestamp.strftime('%Y-%m-%d')}",
        "confidence: Medium",
        "sources:",
    ]

    for source in source_urls:
        chapter_lines.append(f"  - {source}")

    chapter_lines.extend(
        [
            "---",
            "",
            "# Objective",
            f"- Capture source knowledge for topic: {topic} and map it to practical trading use.",
            "",
            "# Core Concepts",
            f"- Source synthesis: {source_title}",
            "",
            "# Extracted Claims",
        ]
    )

    if claims:
        for claim in claims:
            chapter_lines.append(f"- {claim}")
    else:
        chapter_lines.append("- No claim extracted; manual review required.")

    chapter_lines.extend(["", "# Actionable Rules Derived"])
    for insight in trading_insights:
        chapter_lines.append(f"- {insight}")

    chapter_lines.extend(
        [
            "",
            "# Trading Application Notes",
            "- Use insights as scenario context, not as standalone trade triggers.",
            "- Confirm entries with technical structure, liquidity, and risk limits.",
            "",
            "# Constraints And Caveats",
            "- Content is automatically extracted and may contain noise.",
            "- Requires human review before status promotion.",
            "",
            "# Implementation Guidance",
            "- This draft supports Step 2 (ingestion) and Step 3 (chapter placement).",
            "- Step 4 applies after chapter is promoted to Approved.",
            "",
            "# References",
        ]
    )

    for source in source_urls:
        chapter_lines.append(f"- {source}")

    chapter_lines.extend(
        [
            "",
            "# Source Acquisition Mode",
            f"- {'Manual URL ingestion' if source_url else 'Auto multi-source topic research'}",
        ]
    )

    chapter_content = "\n".join(chapter_lines) + "\n"
    chapter_path.write_text(chapter_content, encoding="utf-8")

    topic_index_content = topic_index.read_text(encoding="utf-8")
    source_label = source_url if source_url else "AUTO_DISCOVERY"
    kb._append_kb_changelog(
        f"Ingested topic '{topic}' from {source_label} into {chapter_path.as_posix()}"
    )

    chapter_repo_path = "knowledge-base/" + kb._safe_rel_path(chapter_path, kb.KB_ROOT)
    topic_index_repo_path = "knowledge-base/" + kb._safe_rel_path(topic_index, kb.KB_ROOT)
    commit_msg = f"kb: auto-ingest topic '{topic}' from {source_label}"

    def _do_github_writeback() -> None:
        files_to_commit = {
            chapter_repo_path: chapter_content,
            topic_index_repo_path: topic_index_content,
        }
        ok = kb._github_commit_files(files_to_commit, commit_msg)
        if ok:
            logger.info("GitHub write-back succeeded for topic '%s'", topic)
        else:
            logger.warning(
                "GitHub write-back skipped or failed for topic '%s' (GITHUB_TOKEN configured: %s)",
                topic,
                bool(kb.config.GITHUB_TOKEN),
            )

    threading.Thread(target=_do_github_writeback, daemon=True).start()

    return kb.KnowledgeIngestResponse(
        topic=topic,
        url=source_url if source_url else "AUTO_DISCOVERY",
        source_title=source_title,
        created_chapter=chapter_path.as_posix(),
        created_topic_index=topic_index.as_posix(),
        changelog_updated=kb.KB_CHANGELOG_PATH.as_posix(),
        status="Draft chapter created and changelog updated",
        summary=summary,
    )
