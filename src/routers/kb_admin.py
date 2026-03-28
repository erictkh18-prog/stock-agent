"""Router: KB admin — content maintenance (1.3.2).

Handles chapter lifecycle management (approve / reject / draft) and the
local file explorer integration for quick editing:
  POST /knowledge-base/chapter-status  — update chapter review status
  POST /knowledge-base/open-explorer   — open file in OS explorer
"""

import asyncio
import logging
import threading

from fastapi import APIRouter, Depends, HTTPException

import src.knowledge_base as kb
from src.auth import UserInfo, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["KB - Admin (Content Maintenance)"])


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
