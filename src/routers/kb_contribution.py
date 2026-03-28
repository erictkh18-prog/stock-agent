"""Router: KB content contribution (1.1).

Handles the POST /knowledge-base/ingest endpoint that allows authenticated
contributors to create new knowledge-base chapters from a URL or topic.
"""

import asyncio
import logging
import threading
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

import src.knowledge_base as kb
from src.auth import UserInfo, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["KB - Content Contribution"])


@router.post("/knowledge-base/ingest", response_model=kb.KnowledgeIngestResponse)
async def knowledge_base_ingest(
    payload: kb.KnowledgeIngestRequest,
    current_user: UserInfo = Depends(get_current_user),
):
    """Ingest website content into section/topic/chapter structure.

    Requires a valid authenticated session (Bearer token). Supply a URL to
    extract content from a specific page, or omit the URL to trigger
    auto-discovery across Reuters, Bloomberg, and Investopedia.
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
