"""Router: authentication endpoints."""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, RedirectResponse

import src.auth as auth_module
from src.auth import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserInfo,
    approve_user,
    get_current_user,
    list_all_users,
    list_pending_users,
    login_user,
    reject_user,
    resend_verification,
    revoke_user,
    register_user,
    require_admin,
    verify_email_token,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/auth/register")
async def auth_register(payload: RegisterRequest):
    """Register a new account (pending admin approval).

    The admin email is auto-approved and can log in immediately.
    All other accounts require admin approval via /auth/approve/{email}.
    """
    result = register_user(payload.email, payload.password)
    if result.get("approved") and payload.email.strip().lower() == auth_module.ADMIN_EMAIL:
        token_resp = login_user(payload.email, payload.password)
        result["token"] = token_resp.access_token
    return result


@router.post("/auth/login", response_model=TokenResponse)
async def auth_login(payload: LoginRequest):
    """Authenticate with email and password; returns a JWT Bearer token."""
    return login_user(payload.email, payload.password)


@router.get("/auth/me", response_model=UserInfo)
async def auth_me(current_user: UserInfo = Depends(get_current_user)):
    """Return the currently authenticated user's info."""
    return current_user


@router.post("/auth/approve/{email}")
async def auth_approve(email: str, admin: UserInfo = Depends(require_admin)):
    """Approve a pending user account (admin only)."""
    return approve_user(email)


@router.post("/auth/reject/{email}")
async def auth_reject(email: str, admin: UserInfo = Depends(require_admin)):
    """Reject and remove a pending user account (admin only)."""
    return reject_user(email)


@router.post("/auth/revoke/{email}")
async def auth_revoke(email: str, admin: UserInfo = Depends(require_admin)):
    """Revoke an approved non-admin user account (admin only)."""
    return revoke_user(email)


@router.get("/auth/pending-users")
async def auth_pending_users(admin: UserInfo = Depends(require_admin)):
    """List accounts awaiting approval (admin only)."""
    return list_pending_users()


@router.get("/auth/users")
async def auth_all_users(admin: UserInfo = Depends(require_admin)):
    """List all registered users (admin only)."""
    return list_all_users()


@router.get("/auth/verify-email")
async def auth_verify_email(token: str):
    """Validate an email verification token and redirect to the login page."""
    try:
        verify_email_token(token)
        return RedirectResponse(url="/login?verified=1", status_code=302)
    except Exception:
        return RedirectResponse(url="/login?verified_error=1", status_code=302)


@router.post("/auth/resend-verification")
async def auth_resend_verification(request: Request):
    """Resend a verification email. Accepts JSON body {\"email\": \"...\"}."""
    body = await request.json()
    email = body.get("email", "")
    return resend_verification(email)
