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
