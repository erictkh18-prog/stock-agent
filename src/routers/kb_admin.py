"""Router: KB admin — content maintenance (1.3.2).

Handles chapter lifecycle management (approve / reject / draft) and the
local file explorer integration for quick editing:
  POST /knowledge-base/chapter-status  — update chapter review status
  POST /knowledge-base/open-explorer   — open file in OS explorer
"""

import asyncio
import logging
import threading
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

import src.knowledge_base as kb
from src.auth import UserInfo, get_current_user
from src.auth import require_admin
from src.auth import list_all_users, list_pending_users

logger = logging.getLogger(__name__)

router = APIRouter(tags=["KB - Admin (Content Maintenance)"])


@router.get("/admin/overview")
async def admin_overview(admin: UserInfo = Depends(require_admin)):
    """Return admin dashboard metrics for account queue and KB quality state."""
    users = list_all_users()
    pending = list_pending_users()
    kb_tree = kb._build_kb_tree()

    status_counts = {"draft": 0, "approved": 0, "rejected": 0, "other": 0}
    chapter_rows: list[dict] = []

    for section in kb_tree.get("sections", []):
        for topic in section.get("topics", []):
            for chapter in topic.get("chapters", []):
                status = str(chapter.get("status", "Draft")).strip().lower()
                if status in status_counts:
                    status_counts[status] += 1
                else:
                    status_counts["other"] += 1

                chapter_rows.append(
                    {
                        "section": section.get("name", ""),
                        "topic": topic.get("name", ""),
                        "name": chapter.get("name", ""),
                        "relative_path": chapter.get("relative_path", ""),
                        "status": chapter.get("status", "Draft"),
                        "confidence_band": chapter.get("confidence_band", "Unknown"),
                        "weighted_relevance_score": int(chapter.get("weighted_relevance_score", 0) or 0),
                        "source_quality_score": int(chapter.get("source_quality_score", 0) or 0),
                        "updated_at": chapter.get("updated_at", ""),
                    }
                )

    top_predictive_chapters = sorted(
        chapter_rows,
        key=lambda item: (
            int(item.get("weighted_relevance_score", 0)),
            int(item.get("source_quality_score", 0)),
            str(item.get("updated_at", "")),
        ),
        reverse=True,
    )[:6]

    return {
        "accounts": {
            "total_users": len(users),
            "pending_users": len(pending),
            "approved_users": sum(1 for user in users if user.get("is_approved")),
            "admin_users": sum(1 for user in users if user.get("is_admin")),
        },
        "knowledge_base": {
            "total_sections": kb_tree.get("total_sections", len(kb_tree.get("sections", []))),
            "total_topics": kb_tree.get("total_topics", 0),
            "total_chapters": kb_tree.get("total_chapters", 0),
            "status_counts": status_counts,
            "top_predictive_chapters": top_predictive_chapters,
        },
    }


@router.get("/admin/kb-chapters")
async def admin_kb_chapters(status: str = "all", admin: UserInfo = Depends(require_admin)):
    """Return chapters filtered by status for admin moderation.

    status values: all, Draft, Approved, Rejected
    """
    kb_tree = kb._build_kb_tree()
    chapters: list[dict] = []
    normalized_status = status.strip().lower()

    allowed = {"all", "draft", "approved", "rejected"}
    if normalized_status not in allowed:
        raise HTTPException(status_code=400, detail="Status must be one of: all, Draft, Approved, Rejected")

    for section in kb_tree.get("sections", []):
        for topic in section.get("topics", []):
            for chapter in topic.get("chapters", []):
                chapter_status = str(chapter.get("status", "Draft")).strip()
                if normalized_status == "all" or chapter_status.lower() == normalized_status:
                    chapters.append({
                        "relative_path": chapter.get("relative_path", ""),
                        "name": chapter.get("name", ""),
                        "section": section.get("name", ""),
                        "topic": topic.get("name", ""),
                        "status": chapter_status,
                        "confidence_band": chapter.get("confidence_band", "Unknown"),
                        "weighted_relevance_score": int(chapter.get("weighted_relevance_score", 0) or 0),
                        "source_quality_score": int(chapter.get("source_quality_score", 0) or 0),
                        "updated_at": chapter.get("updated_at", ""),
                    })

    return {
        "status": status,
        "total": len(chapters),
        "chapters": chapters,
    }


@router.get("/admin/kb-chapter-details")
async def admin_kb_chapter_details(path: str, admin: UserInfo = Depends(require_admin)):
    """Return chapter details (including non-approved content) for admin moderation."""
    relative_path = path.strip()
    if not relative_path:
        raise HTTPException(status_code=400, detail="Chapter path is required")

    chapter_path = kb._validate_kb_relative_path(relative_path)
    content = chapter_path.read_text(encoding="utf-8")
    chapter_title = chapter_path.stem
    insights = kb._build_chapter_viewer_insights(content, default_title=chapter_title)

    return {
        "path": kb._safe_rel_path(chapter_path, kb.KB_ROOT),
        "title": chapter_title,
        "status": kb._extract_chapter_status(content),
        "updated_at": datetime.fromtimestamp(chapter_path.stat().st_mtime).isoformat(),
        "summary": insights["summary"],
        "price_movement_analysis": insights["price_movement_analysis"],
        "content": content,
    }


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


@router.post("/knowledge-base/open-explorer", response_model=kb.KnowledgeOpenExplorerResponse)
async def knowledge_base_open_explorer(
    payload: kb.KnowledgeOpenExplorerRequest,
    admin: UserInfo = Depends(require_admin),
):
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
