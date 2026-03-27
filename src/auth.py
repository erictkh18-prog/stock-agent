"""Authentication module for Knowledge Base Builder access control.

Implements JWT-based auth with:
- Bcrypt password hashing
- Admin approval flow for new accounts
- Role-based access (admin vs contributor)
- User store backed by a JSON file (data/kb_users.json)
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "change-me-in-production-use-a-long-random-string")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))  # 8 hours

ADMIN_EMAIL: str = os.getenv("ADMIN_EMAIL", "erictkh18@gmail.com")

_DATA_DIR = Path(__file__).parent.parent / "data"
_USERS_FILE = _DATA_DIR / "kb_users.json"

# ── Password hashing ──────────────────────────────────────────────────────────
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ── Thread safety ─────────────────────────────────────────────────────────────
_users_lock = threading.Lock()

# ── Pydantic models ───────────────────────────────────────────────────────────

class UserRecord(BaseModel):
    email: str
    hashed_password: str
    is_admin: bool = False
    is_approved: bool = False
    created_at: str = ""


class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserInfo(BaseModel):
    email: str
    is_admin: bool
    is_approved: bool


# ── User store ────────────────────────────────────────────────────────────────

def _load_users() -> dict[str, dict]:
    """Load user store from JSON file; return empty dict on missing file."""
    if not _USERS_FILE.exists():
        return {}
    try:
        with _USERS_FILE.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to load kb_users.json: %s", exc)
        return {}


def _save_users(users: dict[str, dict]) -> None:
    """Persist user store to JSON file."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with _USERS_FILE.open("w", encoding="utf-8") as fh:
            json.dump(users, fh, indent=2)
    except OSError as exc:
        logger.error("Failed to save kb_users.json: %s", exc)


def _ensure_admin_exists() -> None:
    """Create the admin user record if it does not already exist.

    The admin account starts as approved but without a password – the admin
    must call /auth/set-password (or register normally) the first time.
    We create a placeholder so the email is recognisable as an admin.
    """
    with _users_lock:
        users = _load_users()
        if ADMIN_EMAIL not in users:
            users[ADMIN_EMAIL] = UserRecord(
                email=ADMIN_EMAIL,
                hashed_password="",  # no password yet
                is_admin=True,
                is_approved=True,
                created_at=datetime.now(timezone.utc).isoformat(),
            ).model_dump()
            _save_users(users)
            logger.info("Admin account placeholder created for %s", ADMIN_EMAIL)


# Bootstrap admin on module import
_ensure_admin_exists()


# ── Auth helpers ──────────────────────────────────────────────────────────────

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def create_access_token(email: str, is_admin: bool) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": email,
        "is_admin": is_admin,
        "exp": expire,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


# ── FastAPI dependencies ──────────────────────────────────────────────────────

_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> UserInfo:
    """Dependency: require a valid JWT; return user info."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_token(credentials.credentials)
    email: str = payload.get("sub", "")
    is_admin: bool = payload.get("is_admin", False)

    with _users_lock:
        users = _load_users()
        user = users.get(email)

    if not user or not user.get("is_approved"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account pending approval or not found",
        )
    return UserInfo(email=email, is_admin=is_admin, is_approved=True)


def require_admin(user: UserInfo = Depends(get_current_user)) -> UserInfo:
    """Dependency: require admin role."""
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


# ── Auth service functions ────────────────────────────────────────────────────

def register_user(email: str, password: str) -> dict:
    """Register a new user (pending admin approval).

    Returns a dict with status info.
    Raises HTTPException on duplicate or invalid input.
    """
    email = email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email address")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    with _users_lock:
        users = _load_users()
        if email in users:
            existing = users[email]
            # Allow the admin to set their password on first login
            if email == ADMIN_EMAIL and not existing.get("hashed_password"):
                existing["hashed_password"] = hash_password(password)
                existing["is_approved"] = True
                existing["is_admin"] = True
                _save_users(users)
                return {
                    "message": "Admin password set. You can now log in.",
                    "approved": True,
                }
            raise HTTPException(status_code=409, detail="Email already registered")

        is_admin = email == ADMIN_EMAIL
        users[email] = UserRecord(
            email=email,
            hashed_password=hash_password(password),
            is_admin=is_admin,
            is_approved=is_admin,  # admin is auto-approved
            created_at=datetime.now(timezone.utc).isoformat(),
        ).model_dump()
        _save_users(users)

    if is_admin:
        return {"message": "Admin account created. You can log in immediately.", "approved": True}

    return {
        "message": "Registration submitted. Awaiting admin approval before you can log in.",
        "approved": False,
    }


def login_user(email: str, password: str) -> TokenResponse:
    """Authenticate user and return JWT token."""
    email = email.strip().lower()

    with _users_lock:
        users = _load_users()
        user = users.get(email)

    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    hashed = user.get("hashed_password", "")
    if not hashed or not verify_password(password, hashed):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.get("is_approved"):
        raise HTTPException(
            status_code=403,
            detail="Account pending admin approval. You will be notified by email.",
        )

    token = create_access_token(email=email, is_admin=user.get("is_admin", False))
    return TokenResponse(access_token=token)


def approve_user(email: str) -> dict:
    """Approve a pending user (admin only)."""
    email = email.strip().lower()
    with _users_lock:
        users = _load_users()
        user = users.get(email)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if user.get("is_approved"):
            return {"message": f"{email} is already approved"}
        user["is_approved"] = True
        _save_users(users)
    return {"message": f"{email} has been approved"}


def list_pending_users() -> list[dict]:
    """Return list of users awaiting approval."""
    with _users_lock:
        users = _load_users()
    return [
        {"email": u["email"], "created_at": u.get("created_at", "")}
        for u in users.values()
        if not u.get("is_approved")
    ]


def list_all_users() -> list[dict]:
    """Return summary of all users (admin only)."""
    with _users_lock:
        users = _load_users()
    return [
        {
            "email": u["email"],
            "is_admin": u.get("is_admin", False),
            "is_approved": u.get("is_approved", False),
            "created_at": u.get("created_at", ""),
        }
        for u in users.values()
    ]
