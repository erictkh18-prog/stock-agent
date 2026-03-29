"""Router: KB content viewer (1.2).

Handles read-only browsing of the knowledge base:
  GET /knowledge-base/index   — full section/topic/chapter tree
  GET /knowledge-base/chapter — single chapter markdown content
"""

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

import src.knowledge_base as kb

logger = logging.getLogger(__name__)

router = APIRouter(tags=["KB - Content Viewer"])


def _approved_only_tree(tree: dict) -> dict:
    approved_sections: list[dict] = []
    total_topics = 0
    total_chapters = 0

    for section in tree.get("sections", []):
        approved_topics: list[dict] = []
        for topic in section.get("topics", []):
            approved_chapters = [
                chapter
                for chapter in topic.get("chapters", [])
                if str(chapter.get("status", "Draft")).strip().lower() == "approved"
            ]
            if not approved_chapters:
                continue

            topic_copy = dict(topic)
            topic_copy["chapters"] = approved_chapters
            topic_copy["chapter_count"] = len(approved_chapters)
            approved_topics.append(topic_copy)
            total_topics += 1
            total_chapters += len(approved_chapters)

        if not approved_topics:
            continue

        section_copy = dict(section)
        section_copy["topics"] = approved_topics
        section_copy["topic_count"] = len(approved_topics)
        approved_sections.append(section_copy)

    return {
        "kb_root": tree.get("kb_root", "knowledge-base"),
        "sections": approved_sections,
        "total_sections": len(approved_sections),
        "total_topics": total_topics,
        "total_chapters": total_chapters,
    }


@router.get("/knowledge-base/index")
async def knowledge_base_index():
    """Return section/topic/chapter tree for knowledge-base browsing."""
    if not kb.KB_ROOT.exists():
        raise HTTPException(status_code=404, detail="Knowledge base root not found")
    return _approved_only_tree(kb._build_kb_tree())


@router.get("/knowledge-base/chapter")
async def knowledge_base_chapter(
    path: str = Query(..., description="Knowledge-base relative markdown path"),
):
    """Return chapter markdown content and metadata for viewer rendering."""
    if not path.strip():
        raise HTTPException(status_code=400, detail="Chapter path is required")

    chapter_path = kb._validate_kb_relative_path(path.strip())
    content = chapter_path.read_text(encoding="utf-8")
    chapter_status = kb._extract_chapter_status(chapter_path)
    if chapter_status.strip().lower() != "approved":
        raise HTTPException(status_code=404, detail="Chapter not available")

    chapter_title = chapter_path.stem
    insights = kb._build_chapter_viewer_insights(content, default_title=chapter_title)

    return {
        "path": kb._safe_rel_path(chapter_path, kb.KB_ROOT),
        "title": chapter_title,
        "updated_at": datetime.fromtimestamp(chapter_path.stat().st_mtime).isoformat(),
        "summary": insights["summary"],
        "price_movement_analysis": insights["price_movement_analysis"],
        "content_level": insights["content_level"],
        "trading_implications": insights["trading_implications"],
        "relevance_tier": insights["relevance_tier"],
        "content": content,
    }
