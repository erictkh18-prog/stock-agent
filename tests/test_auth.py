"""Tests for KB Builder authentication endpoints.

Covers:
- User registration (admin auto-approved, others pending)
- Login (valid credentials, wrong password, pending account)
- /auth/me endpoint (valid token, no token)
- Admin-only endpoints (approve user, list pending, list all)
- /knowledge-base/ingest requires auth (401 without token)
- /knowledge-base/chapter-status Approved requires admin
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# Patch the users file path before importing app so we use a temp file
_tmp_dir = tempfile.mkdtemp()
_tmp_users_file = Path(_tmp_dir) / "kb_users.json"

with patch.dict(os.environ, {
    "ADMIN_EMAIL": "admin@test.com",
    "JWT_SECRET_KEY": "test-secret-key-for-unit-tests-only",
}):
    import src.auth as auth_module
    # Point auth module at temp file
    auth_module._USERS_FILE = _tmp_users_file
    auth_module._DATA_DIR = Path(_tmp_dir)
    auth_module.ADMIN_EMAIL = "admin@test.com"
    # Re-bootstrap admin placeholder
    auth_module._ensure_admin_exists()

    from src.main import app

client = TestClient(app, raise_server_exceptions=True)


def _reset_users():
    """Clear the temp user store between tests."""
    if _tmp_users_file.exists():
        _tmp_users_file.unlink()
    auth_module._ensure_admin_exists()


@pytest.fixture(autouse=True)
def fresh_user_store():
    _reset_users()
    yield
    _reset_users()


# ── Registration ──────────────────────────────────────────────────────────────

def test_register_admin_auto_approved():
    resp = client.post("/auth/register", json={"email": "admin@test.com", "password": "AdminPass1!"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["approved"] is True
    assert "token" in data


def test_register_non_admin_pending():
    resp = client.post("/auth/register", json={"email": "user@example.com", "password": "UserPass1!"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["approved"] is False
    assert "token" not in data
    assert data.get("email_verification_sent") is True


def test_register_duplicate_email():
    client.post("/auth/register", json={"email": "user@example.com", "password": "UserPass1!"})
    resp = client.post("/auth/register", json={"email": "user@example.com", "password": "AnotherPass!"})
    assert resp.status_code == 409


def test_register_short_password():
    resp = client.post("/auth/register", json={"email": "user@example.com", "password": "short"})
    assert resp.status_code == 400


def test_register_invalid_email():
    resp = client.post("/auth/register", json={"email": "notanemail", "password": "ValidPass1!"})
    assert resp.status_code == 400


# ── Login ─────────────────────────────────────────────────────────────────────

def test_login_admin_success():
    client.post("/auth/register", json={"email": "admin@test.com", "password": "AdminPass1!"})
    resp = client.post("/auth/login", json={"email": "admin@test.com", "password": "AdminPass1!"})
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


def test_login_wrong_password():
    client.post("/auth/register", json={"email": "admin@test.com", "password": "AdminPass1!"})
    resp = client.post("/auth/login", json={"email": "admin@test.com", "password": "WrongPass!"})
    assert resp.status_code == 401


def test_login_unknown_email():
    resp = client.post("/auth/login", json={"email": "nobody@example.com", "password": "AnyPass1!"})
    assert resp.status_code == 401


def test_login_pending_account_blocked():
    client.post("/auth/register", json={"email": "user@example.com", "password": "UserPass1!"})
    resp = client.post("/auth/login", json={"email": "user@example.com", "password": "UserPass1!"})
    # Blocked because email not yet verified
    assert resp.status_code == 403


# ── Email verification ────────────────────────────────────────────────────────

def test_verify_email_success_redirects():
    client.post("/auth/register", json={"email": "user@example.com", "password": "UserPass1!"})
    token = auth_module.generate_verification_token("user@example.com")
    resp = client.get(f"/auth/verify-email?token={token}", follow_redirects=False)
    assert resp.status_code == 302
    assert "verified=1" in resp.headers["location"]


def test_verify_email_then_still_blocked_until_admin_approval():
    client.post("/auth/register", json={"email": "user@example.com", "password": "UserPass1!"})
    _verify_user("user@example.com")
    # Email verified but not yet admin-approved
    resp = client.post("/auth/login", json={"email": "user@example.com", "password": "UserPass1!"})
    assert resp.status_code == 403
    assert "approval" in resp.json()["detail"].lower()


def test_verify_email_invalid_token_redirects_error():
    resp = client.get("/auth/verify-email?token=invalid-token-xyz", follow_redirects=False)
    assert resp.status_code == 302
    assert "verified_error=1" in resp.headers["location"]


def test_verify_email_token_is_single_use():
    client.post("/auth/register", json={"email": "user@example.com", "password": "UserPass1!"})
    token = auth_module.generate_verification_token("user@example.com")
    client.get(f"/auth/verify-email?token={token}", follow_redirects=False)
    # Second use of the same token must fail
    resp = client.get(f"/auth/verify-email?token={token}", follow_redirects=False)
    assert resp.status_code == 302
    assert "verified_error=1" in resp.headers["location"]


def test_resend_verification_returns_ok():
    client.post("/auth/register", json={"email": "user@example.com", "password": "UserPass1!"})
    resp = client.post(
        "/auth/resend-verification",
        json={"email": "user@example.com"},
    )
    assert resp.status_code == 200
    assert "link" in resp.json()["message"].lower()


def test_resend_verification_unknown_email_generic_response():
    resp = client.post(
        "/auth/resend-verification",
        json={"email": "nobody@example.com"},
    )
    assert resp.status_code == 200  # Always generic — no user enumeration


def test_full_verification_and_approve_flow():
    admin_token = _get_admin_token()
    client.post("/auth/register", json={"email": "user@example.com", "password": "UserPass1!"})
    _verify_user("user@example.com")
    client.post(
        "/auth/approve/user@example.com",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    login = client.post("/auth/login", json={"email": "user@example.com", "password": "UserPass1!"})
    assert login.status_code == 200
    assert "access_token" in login.json()


# ── /auth/me ──────────────────────────────────────────────────────────────────

def test_auth_me_with_valid_token():
    client.post("/auth/register", json={"email": "admin@test.com", "password": "AdminPass1!"})
    login = client.post("/auth/login", json={"email": "admin@test.com", "password": "AdminPass1!"})
    token = login.json()["access_token"]

    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "admin@test.com"
    assert data["is_admin"] is True


def test_auth_me_without_token():
    resp = client.get("/auth/me")
    assert resp.status_code == 401


def test_admin_approvals_page_available():
    resp = client.get("/admin/approvals")
    assert resp.status_code == 200
    assert "Admin - Account Approvals" in resp.text


# ── Admin approval flow ───────────────────────────────────────────────────────

def _verify_user(email: str) -> None:
    """Fast-path email verification in tests (bypasses SMTP — generates token directly)."""
    token = auth_module.generate_verification_token(email)
    client.get(f"/auth/verify-email?token={token}", follow_redirects=False)


def _get_admin_token():
    client.post("/auth/register", json={"email": "admin@test.com", "password": "AdminPass1!"})
    resp = client.post("/auth/login", json={"email": "admin@test.com", "password": "AdminPass1!"})
    return resp.json()["access_token"]


def test_approve_user_as_admin():
    admin_token = _get_admin_token()
    client.post("/auth/register", json={"email": "user@example.com", "password": "UserPass1!"})
    _verify_user("user@example.com")

    resp = client.post(
        "/auth/approve/user@example.com",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    # User can now log in
    login = client.post("/auth/login", json={"email": "user@example.com", "password": "UserPass1!"})
    assert login.status_code == 200


def test_approve_user_non_admin_forbidden():
    admin_token = _get_admin_token()
    # Register and approve a regular user
    client.post("/auth/register", json={"email": "user@example.com", "password": "UserPass1!"})
    _verify_user("user@example.com")
    client.post(
        "/auth/approve/user@example.com",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    user_login = client.post("/auth/login", json={"email": "user@example.com", "password": "UserPass1!"})
    user_token = user_login.json()["access_token"]

    # Register another user
    client.post("/auth/register", json={"email": "other@example.com", "password": "OtherPass1!"})

    resp = client.post(
        "/auth/approve/other@example.com",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert resp.status_code == 403


def test_reject_user_as_admin():
    admin_token = _get_admin_token()
    client.post("/auth/register", json={"email": "rejectme@example.com", "password": "UserPass1!"})

    reject_resp = client.post(
        "/auth/reject/rejectme@example.com",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert reject_resp.status_code == 200

    login = client.post("/auth/login", json={"email": "rejectme@example.com", "password": "UserPass1!"})
    assert login.status_code == 401


def test_reject_user_non_admin_forbidden():
    admin_token = _get_admin_token()
    client.post("/auth/register", json={"email": "user@example.com", "password": "UserPass1!"})
    _verify_user("user@example.com")
    client.post(
        "/auth/approve/user@example.com",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    user_login = client.post("/auth/login", json={"email": "user@example.com", "password": "UserPass1!"})
    user_token = user_login.json()["access_token"]

    client.post("/auth/register", json={"email": "pending@example.com", "password": "Pending1!"})
    resp = client.post(
        "/auth/reject/pending@example.com",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert resp.status_code == 403


def test_revoke_user_as_admin():
    admin_token = _get_admin_token()
    client.post("/auth/register", json={"email": "approved@example.com", "password": "UserPass1!"})
    _verify_user("approved@example.com")
    client.post(
        "/auth/approve/approved@example.com",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    revoke_resp = client.post(
        "/auth/revoke/approved@example.com",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert revoke_resp.status_code == 200

    login = client.post("/auth/login", json={"email": "approved@example.com", "password": "UserPass1!"})
    assert login.status_code == 403


def test_revoke_user_non_admin_forbidden():
    admin_token = _get_admin_token()
    client.post("/auth/register", json={"email": "user@example.com", "password": "UserPass1!"})
    _verify_user("user@example.com")
    client.post(
        "/auth/approve/user@example.com",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    user_login = client.post("/auth/login", json={"email": "user@example.com", "password": "UserPass1!"})
    user_token = user_login.json()["access_token"]

    client.post("/auth/register", json={"email": "approved2@example.com", "password": "UserPass2!"})
    _verify_user("approved2@example.com")
    client.post(
        "/auth/approve/approved2@example.com",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    resp = client.post(
        "/auth/revoke/approved2@example.com",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert resp.status_code == 403


def test_list_pending_users():
    admin_token = _get_admin_token()
    client.post("/auth/register", json={"email": "user1@example.com", "password": "UserPass1!"})
    client.post("/auth/register", json={"email": "user2@example.com", "password": "UserPass2!"})

    resp = client.get("/auth/pending-users", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    emails = [u["email"] for u in resp.json()]
    assert "user1@example.com" in emails
    assert "user2@example.com" in emails


def test_list_all_users():
    admin_token = _get_admin_token()
    client.post("/auth/register", json={"email": "user@example.com", "password": "UserPass1!"})

    resp = client.get("/auth/users", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    emails = [u["email"] for u in resp.json()]
    assert "admin@test.com" in emails
    assert "user@example.com" in emails


def test_admin_overview_requires_admin():
    admin_token = _get_admin_token()
    client.post("/auth/register", json={"email": "user@example.com", "password": "UserPass1!"})
    _verify_user("user@example.com")
    client.post(
        "/auth/approve/user@example.com",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    user_login = client.post("/auth/login", json={"email": "user@example.com", "password": "UserPass1!"})
    user_token = user_login.json()["access_token"]

    resp = client.get("/admin/overview", headers={"Authorization": f"Bearer {user_token}"})
    assert resp.status_code == 403


def test_admin_overview_returns_dashboard_payload():
    admin_token = _get_admin_token()
    resp = client.get("/admin/overview", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert "accounts" in data
    assert "knowledge_base" in data
    assert "pending_users" in data["accounts"]
    assert "status_counts" in data["knowledge_base"]
    assert "top_predictive_chapters" in data["knowledge_base"]


# ── KB ingest endpoint requires auth ─────────────────────────────────────────

def test_kb_ingest_without_token_returns_401():
    resp = client.post(
        "/knowledge-base/ingest",
        json={"topic": "Test topic", "url": ""},
    )
    assert resp.status_code == 401


def test_kb_ingest_with_pending_account_returns_403():
    client.post("/auth/register", json={"email": "user@example.com", "password": "UserPass1!"})
    # Manually create a token for the unapproved user using auth_module directly
    token = auth_module.create_access_token("user@example.com", is_admin=False)
    resp = client.post(
        "/knowledge-base/ingest",
        json={"topic": "Test topic", "url": ""},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


# ── Chapter status Approve requires admin ────────────────────────────────────

def test_chapter_status_approve_requires_admin(tmp_path):
    admin_token = _get_admin_token()
    # Register, verify, and approve a regular user
    client.post("/auth/register", json={"email": "user@example.com", "password": "UserPass1!"})
    _verify_user("user@example.com")
    client.post(
        "/auth/approve/user@example.com",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    user_login = client.post("/auth/login", json={"email": "user@example.com", "password": "UserPass1!"})
    user_token = user_login.json()["access_token"]

    resp = client.post(
        "/knowledge-base/chapter-status",
        json={"path": "sections/some/chapters/file.md", "status": "Approved"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert resp.status_code == 403


def test_chapter_status_approve_allowed_for_admin(monkeypatch, tmp_path):
    admin_token = _get_admin_token()

    fake_chapter = tmp_path / "fake_chapter.md"
    fake_chapter.write_text("---\nstatus: Draft\n---\n# Test\n", encoding="utf-8")
    rel_path = "sections/fundamentals/chapters/test.md"

    class _NoOpThread:
        def __init__(self, **kwargs):
            pass
        def start(self):
            pass

    import asyncio

    async def _noop_to_thread(func, *args, **kwargs):
        return None

    monkeypatch.setattr("src.knowledge_base._validate_kb_relative_path", lambda p: fake_chapter)
    monkeypatch.setattr("src.knowledge_base._safe_rel_path", lambda *a: rel_path)
    monkeypatch.setattr("src.knowledge_base._apply_chapter_status_update", lambda *a, **kw: None)
    monkeypatch.setattr("src.knowledge_base._append_kb_changelog", lambda *a: None)
    monkeypatch.setattr("src.routers.kb_admin.threading.Thread", _NoOpThread)
    monkeypatch.setattr("src.routers.kb_admin.asyncio.to_thread", _noop_to_thread)

    resp = client.post(
        "/knowledge-base/chapter-status",
        json={"path": rel_path, "status": "Approved"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["chapter_status"] == "Approved"


# ── Login page served ─────────────────────────────────────────────────────────

def test_login_page_accessible():
    resp = client.get("/login")
    assert resp.status_code == 200
    assert b"Sign In" in resp.content or b"login" in resp.content.lower()
