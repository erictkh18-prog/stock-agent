"""Authentication module for Knowledge Base Builder access control.

Implements JWT-based auth with:
- Bcrypt password hashing
- Admin approval flow for new accounts
- Role-based access (admin vs contributor)
- Persistent Postgres storage when AUTH_DATABASE_URL or DATABASE_URL is set
- JSON-file fallback for local development and tests
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from passlib.exc import UnknownHashError
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "change-me-in-production-use-a-long-random-string")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))
ADMIN_EMAIL: str = os.getenv("ADMIN_EMAIL", "erictkh18@gmail.com")
AUTH_DATABASE_URL: str = os.getenv("AUTH_DATABASE_URL", os.getenv("DATABASE_URL", "")).strip()

_DATA_DIR = Path(__file__).parent.parent / "data"
_USERS_FILE = _DATA_DIR / "kb_users.json"

# ── Password hashing ──────────────────────────────────────────────────────────
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ── Thread safety ─────────────────────────────────────────────────────────────
_users_lock = threading.Lock()


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


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def verify_password(plain: str, hashed: str) -> bool:
    if not plain or not hashed:
        return False
    try:
        return pwd_context.verify(plain, hashed)
    except (UnknownHashError, ValueError):
        return False


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def _is_postgres_enabled() -> bool:
    return bool(AUTH_DATABASE_URL)


def _load_users_from_json() -> dict[str, dict]:
    if not _USERS_FILE.exists():
        return {}
    try:
        with _USERS_FILE.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to load kb_users.json: %s", exc)
        return {}


def _save_users_to_json(users: dict[str, dict]) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with _USERS_FILE.open("w", encoding="utf-8") as fh:
            json.dump(users, fh, indent=2)
    except OSError as exc:
        logger.error("Failed to save kb_users.json: %s", exc)


def _import_psycopg():
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "AUTH_DATABASE_URL/DATABASE_URL is set but psycopg is not installed. "
            "Install requirements.txt before starting the app."
        ) from exc
    return psycopg


def _connect_postgres():
    psycopg = _import_psycopg()
    return psycopg.connect(AUTH_DATABASE_URL, connect_timeout=10, autocommit=True)


def _normalize_user_dict(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        data = dict(raw)
    else:
        data = {
            "email": raw[0],
            "hashed_password": raw[1],
            "is_admin": raw[2],
            "is_approved": raw[3],
            "created_at": raw[4],
        }

    created_at = data.get("created_at", "")
    if created_at and not isinstance(created_at, str):
        created_at = created_at.isoformat()

    return {
        "email": str(data.get("email", "")).strip().lower(),
        "hashed_password": data.get("hashed_password", "") or "",
        "is_admin": bool(data.get("is_admin", False)),
        "is_approved": bool(data.get("is_approved", False)),
        "created_at": created_at or _utcnow_iso(),
    }


def _ensure_postgres_schema() -> None:
    with _connect_postgres() as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS kb_users (
                email TEXT PRIMARY KEY,
                hashed_password TEXT NOT NULL DEFAULT '',
                is_admin BOOLEAN NOT NULL DEFAULT FALSE,
                is_approved BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_kb_users_pending
            ON kb_users (is_approved, created_at)
            """
        )


def _migrate_json_users_to_postgres() -> None:
    if not _USERS_FILE.exists():
        return

    users = _load_users_from_json()
    if not users:
        return

    with _connect_postgres() as conn, conn.cursor() as cur:
        for user in users.values():
            normalized = _normalize_user_dict(user)
            cur.execute(
                """
                INSERT INTO kb_users (email, hashed_password, is_admin, is_approved, created_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (email) DO NOTHING
                """,
                (
                    normalized["email"],
                    normalized["hashed_password"],
                    normalized["is_admin"],
                    normalized["is_approved"],
                    normalized["created_at"],
                ),
            )


def _ensure_storage_ready() -> None:
    if not _is_postgres_enabled():
        return
    _ensure_postgres_schema()
    _migrate_json_users_to_postgres()


def _list_users() -> list[dict[str, Any]]:
    if _is_postgres_enabled():
        _ensure_storage_ready()
        with _connect_postgres() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT email, hashed_password, is_admin, is_approved, created_at
                FROM kb_users
                ORDER BY created_at ASC, email ASC
                """
            )
            return [_normalize_user_dict(row) for row in cur.fetchall()]

    return [_normalize_user_dict(user) for user in _load_users_from_json().values()]


def _get_user(email: str) -> Optional[dict[str, Any]]:
    normalized_email = email.strip().lower()
    if _is_postgres_enabled():
        _ensure_storage_ready()
        with _connect_postgres() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT email, hashed_password, is_admin, is_approved, created_at
                FROM kb_users
                WHERE email = %s
                """,
                (normalized_email,),
            )
            row = cur.fetchone()
            return _normalize_user_dict(row) if row else None

    user = _load_users_from_json().get(normalized_email)
    return _normalize_user_dict(user) if user else None


def _upsert_user(user: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_user_dict(user)
    if _is_postgres_enabled():
        _ensure_storage_ready()
        with _connect_postgres() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO kb_users (email, hashed_password, is_admin, is_approved, created_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (email) DO UPDATE SET
                    hashed_password = EXCLUDED.hashed_password,
                    is_admin = EXCLUDED.is_admin,
                    is_approved = EXCLUDED.is_approved,
                    created_at = COALESCE(kb_users.created_at, EXCLUDED.created_at)
                """,
                (
                    normalized["email"],
                    normalized["hashed_password"],
                    normalized["is_admin"],
                    normalized["is_approved"],
                    normalized["created_at"],
                ),
            )
        return normalized

    users = _load_users_from_json()
    users[normalized["email"]] = normalized
    _save_users_to_json(users)
    return normalized


def _ensure_admin_exists() -> None:
    """Ensure the admin account exists in the active storage backend."""
    admin_plain_pw: str = os.getenv("ADMIN_PASSWORD", "")
    with _users_lock:
        existing = _get_user(ADMIN_EMAIL)
        if existing is None:
            _upsert_user(
                UserRecord(
                    email=ADMIN_EMAIL,
                    hashed_password=hash_password(admin_plain_pw) if admin_plain_pw else "",
                    is_admin=True,
                    is_approved=True,
                    created_at=_utcnow_iso(),
                ).model_dump()
            )
            logger.info(
                "Admin account created for %s using %s storage",
                ADMIN_EMAIL,
                "Postgres" if _is_postgres_enabled() else "JSON",
            )
            return

        if admin_plain_pw and not verify_password(admin_plain_pw, existing.get("hashed_password", "")):
            existing["hashed_password"] = hash_password(admin_plain_pw)
            existing["is_admin"] = True
            existing["is_approved"] = True
            _upsert_user(existing)
            logger.info("Admin password refreshed from ADMIN_PASSWORD env var")
        elif not existing.get("is_admin") or not existing.get("is_approved"):
            existing["is_admin"] = True
            existing["is_approved"] = True
            _upsert_user(existing)


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


_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> UserInfo:
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
        user = _get_user(email)

    if not user or not user.get("is_approved"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account pending approval or not found",
        )
    return UserInfo(email=email, is_admin=is_admin, is_approved=True)


def require_admin(user: UserInfo = Depends(get_current_user)) -> UserInfo:
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


def register_user(email: str, password: str) -> dict:
    email = email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email address")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    with _users_lock:
        existing = _get_user(email)
        if existing:
            if email == ADMIN_EMAIL and not existing.get("hashed_password"):
                existing["hashed_password"] = hash_password(password)
                existing["is_approved"] = True
                existing["is_admin"] = True
                _upsert_user(existing)
                return {
                    "message": "Admin password set. You can now log in.",
                    "approved": True,
                }
            raise HTTPException(status_code=409, detail="Email already registered")

        is_admin = email == ADMIN_EMAIL
        _upsert_user(
            UserRecord(
                email=email,
                hashed_password=hash_password(password),
                is_admin=is_admin,
                is_approved=is_admin,
                created_at=_utcnow_iso(),
            ).model_dump()
        )

    if is_admin:
        return {"message": "Admin account created. You can log in immediately.", "approved": True}

    return {
        "message": "Registration submitted. Awaiting admin approval before you can log in.",
        "approved": False,
    }


def login_user(email: str, password: str) -> TokenResponse:
    email = email.strip().lower()

    with _users_lock:
        user = _get_user(email)

    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    hashed = user.get("hashed_password", "")
    if not verify_password(password, hashed):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.get("is_approved"):
        raise HTTPException(
            status_code=403,
            detail="Account pending admin approval. You will be notified by email.",
        )

    token = create_access_token(email=email, is_admin=user.get("is_admin", False))
    return TokenResponse(access_token=token)


def approve_user(email: str) -> dict:
    email = email.strip().lower()
    with _users_lock:
        user = _get_user(email)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if user.get("is_approved"):
            return {"message": f"{email} is already approved"}
        user["is_approved"] = True
        _upsert_user(user)
    return {"message": f"{email} has been approved"}


def list_pending_users() -> list[dict]:
    with _users_lock:
        users = _list_users()
    return [
        {"email": u["email"], "created_at": u.get("created_at", "")}
        for u in users
        if not u.get("is_approved")
    ]


def list_all_users() -> list[dict]:
    with _users_lock:
        users = _list_users()
    return [
        {
            "email": u["email"],
            "is_admin": u.get("is_admin", False),
            "is_approved": u.get("is_approved", False),
            "created_at": u.get("created_at", ""),
        }
        for u in users
    ]


# Initialize admin at import time with graceful error handling
# If database connection fails, the app will still start and retry on first request
try:
    _ensure_admin_exists()
except Exception as exc:
    logger.warning(
        "Could not initialize admin at startup (will retry on first auth request): %s",
        exc,
    )