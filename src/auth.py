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
import secrets
import smtplib
import threading
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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

# ── Email verification config ─────────────────────────────────────────────────
SMTP_HOST: str = os.getenv("SMTP_HOST", "")
SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER: str = os.getenv("SMTP_USER", "")
SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM: str = os.getenv("SMTP_FROM", "") or SMTP_USER or "noreply@stock-agent.app"
APP_BASE_URL: str = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")
EMAIL_VERIFY_TOKEN_TTL_HOURS: int = int(os.getenv("EMAIL_VERIFY_TOKEN_TTL_HOURS", "24"))

_DATA_DIR = Path(__file__).parent.parent / "data"
_USERS_FILE = _DATA_DIR / "kb_users.json"

# ── Password hashing ──────────────────────────────────────────────────────────
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ── Thread safety ─────────────────────────────────────────────────────────────
_users_lock = threading.Lock()
_verif_lock = threading.Lock()

# In-memory verification token store (used when Postgres is not available)
# Maps token_str -> {"email": str, "expires_at": datetime}
_PENDING_VERIFICATIONS: dict[str, dict] = {}


class UserRecord(BaseModel):
    email: str
    hashed_password: str
    is_admin: bool = False
    is_approved: bool = False
    email_verified: bool = False
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
            "email_verified": raw[4],
            "created_at": raw[5],
        }

    created_at = data.get("created_at", "")
    if created_at and not isinstance(created_at, str):
        created_at = created_at.isoformat()

    is_approved = bool(data.get("is_approved", False))
    # Backward compatibility: accounts approved before email verification was
    # introduced are automatically considered email-verified so they keep working.
    raw_verified = data.get("email_verified", None)
    if raw_verified is None:
        email_verified = is_approved
    else:
        email_verified = bool(raw_verified)

    return {
        "email": str(data.get("email", "")).strip().lower(),
        "hashed_password": data.get("hashed_password", "") or "",
        "is_admin": bool(data.get("is_admin", False)),
        "is_approved": is_approved,
        "email_verified": email_verified,
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
                email_verified BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        # Migration: add email_verified column to existing tables and
        # grandfather all already-approved accounts as verified.
        cur.execute(
            "ALTER TABLE kb_users ADD COLUMN IF NOT EXISTS "
            "email_verified BOOLEAN NOT NULL DEFAULT FALSE"
        )
        cur.execute(
            "UPDATE kb_users SET email_verified = TRUE "
            "WHERE is_approved = TRUE AND email_verified = FALSE"
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_kb_users_pending
            ON kb_users (is_approved, created_at)
            """
        )
        # Email verification tokens table
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS kb_email_tokens (
                token TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_kb_email_tokens_email
            ON kb_email_tokens (email)
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
    try:
        _ensure_postgres_schema()
        _migrate_json_users_to_postgres()
    except Exception as exc:
        logger.error("Failed to initialize Postgres storage (will use JSON fallback): %s", exc)


def _list_users() -> list[dict[str, Any]]:
    if _is_postgres_enabled():
        try:
            _ensure_storage_ready()
            with _connect_postgres() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT email, hashed_password, is_admin, is_approved, email_verified, created_at
                    FROM kb_users
                    ORDER BY created_at ASC, email ASC
                    """
                )
                return [_normalize_user_dict(row) for row in cur.fetchall()]
        except Exception as exc:
            logger.warning("Postgres query failed, falling back to JSON (error: %s)", exc)

    return [_normalize_user_dict(user) for user in _load_users_from_json().values()]


def _get_user(email: str) -> Optional[dict[str, Any]]:
    normalized_email = email.strip().lower()
    if _is_postgres_enabled():
        try:
            _ensure_storage_ready()
            with _connect_postgres() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT email, hashed_password, is_admin, is_approved, email_verified, created_at
                    FROM kb_users
                    WHERE email = %s
                    """,
                    (normalized_email,),
                )
                row = cur.fetchone()
                return _normalize_user_dict(row) if row else None
        except Exception as exc:
            logger.warning("Postgres query failed, falling back to JSON (error: %s)", exc)

    user = _load_users_from_json().get(normalized_email)
    return _normalize_user_dict(user) if user else None


def _upsert_user(user: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_user_dict(user)
    if _is_postgres_enabled():
        try:
            _ensure_storage_ready()
            with _connect_postgres() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO kb_users (email, hashed_password, is_admin, is_approved, email_verified, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (email) DO UPDATE SET
                        hashed_password = EXCLUDED.hashed_password,
                        is_admin = EXCLUDED.is_admin,
                        is_approved = EXCLUDED.is_approved,
                        email_verified = EXCLUDED.email_verified,
                        created_at = COALESCE(kb_users.created_at, EXCLUDED.created_at)
                    """,
                    (
                        normalized["email"],
                        normalized["hashed_password"],
                        normalized["is_admin"],
                        normalized["is_approved"],
                        normalized["email_verified"],
                        normalized["created_at"],
                    ),
                )
            return normalized
        except Exception as exc:
            logger.warning("Postgres insert failed, falling back to JSON (error: %s)", exc)

    users = _load_users_from_json()
    users[normalized["email"]] = normalized
    _save_users_to_json(users)
    return normalized


def _delete_user(email: str) -> bool:
    normalized_email = email.strip().lower()
    if not normalized_email:
        return False

    if _is_postgres_enabled():
        try:
            _ensure_storage_ready()
            with _connect_postgres() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM kb_users WHERE email = %s", (normalized_email,))
                return cur.rowcount > 0
        except Exception as exc:
            logger.warning("Postgres delete failed, falling back to JSON (error: %s)", exc)

    users = _load_users_from_json()
    if normalized_email not in users:
        return False
    users.pop(normalized_email, None)
    _save_users_to_json(users)
    return True


# ── Email verification ──────────────────────────────────────────────────────

def _store_verification_token(token: str, email: str, expires_at: datetime) -> None:
    """Persist a verification token to Postgres (if available) or in-memory."""
    if _is_postgres_enabled():
        try:
            _ensure_storage_ready()
            with _connect_postgres() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO kb_email_tokens (token, email, expires_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (token) DO NOTHING
                    """,
                    (token, email.strip().lower(), expires_at),
                )
            return
        except Exception as exc:
            logger.warning("Postgres token store failed, using in-memory: %s", exc)

    with _verif_lock:
        _PENDING_VERIFICATIONS[token] = {
            "email": email.strip().lower(),
            "expires_at": expires_at,
        }


def _consume_verification_token(token: str) -> str:
    """Validate and consume a verification token. Returns the email or raises HTTPException."""
    now = datetime.now(timezone.utc)

    if _is_postgres_enabled():
        try:
            _ensure_storage_ready()
            with _connect_postgres() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT email, expires_at FROM kb_email_tokens WHERE token = %s",
                    (token,),
                )
                row = cur.fetchone()
                if not row:
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid or already-used verification link",
                    )
                email_db, expires_at = row[0], row[1]
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                cur.execute("DELETE FROM kb_email_tokens WHERE token = %s", (token,))
                if now > expires_at:
                    raise HTTPException(
                        status_code=400,
                        detail="Verification link has expired. Please register again.",
                    )
                return email_db
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("Postgres token consume failed, checking in-memory: %s", exc)

    with _verif_lock:
        record = _PENDING_VERIFICATIONS.pop(token, None)

    if not record:
        raise HTTPException(
            status_code=400,
            detail="Invalid or already-used verification link",
        )

    expires_at = record["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if now > expires_at:
        raise HTTPException(
            status_code=400,
            detail="Verification link has expired. Please register again.",
        )
    return record["email"]


def generate_verification_token(email: str) -> str:
    """Create a fresh verification token (TTL from EMAIL_VERIFY_TOKEN_TTL_HOURS). Returns the token."""
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=EMAIL_VERIFY_TOKEN_TTL_HOURS)
    _store_verification_token(token, email.strip().lower(), expires_at)
    return token


def send_verification_email(email: str, token: str, base_url: str) -> None:
    """Send an HTML verification email via SMTP, or log the link if SMTP is not configured."""
    verify_url = f"{base_url}/auth/verify-email?token={token}"

    if not SMTP_HOST:
        logger.info(
            "SMTP not configured — verification link for %s: %s",
            email,
            verify_url,
        )
        return

    subject = "Verify your email – Stock Agent KB Builder"
    body_plain = (
        f"Verify your email address:\n{verify_url}\n\n"
        f"This link expires in {EMAIL_VERIFY_TOKEN_TTL_HOURS} hours.\n\n"
        "If you did not request this, you can ignore this email."
    )
    body_html = (
        "<html><body style='font-family:sans-serif;max-width:520px;margin:auto;padding:24px'>"
        "<h2 style='color:#0f766e'>Verify your email address</h2>"
        "<p>You requested access to the Stock Agent Knowledge Base Builder.</p>"
        "<p>Click the button below to verify your email address. "
        f"This link expires in {EMAIL_VERIFY_TOKEN_TTL_HOURS} hours.</p>"
        "<p style='margin:24px 0'>"
        f"  <a href='{verify_url}' style='background:#0f766e;color:#fff;padding:12px 24px;"
        "text-decoration:none;border-radius:8px;font-weight:700'>Verify Email Address</a></p>"
        f"<p style='color:#6b7280;font-size:0.85rem'>Or copy this link:<br><code>{verify_url}</code></p>"
        "<p style='color:#6b7280;font-size:0.85rem'>If you did not request this, you can ignore this email.</p>"
        "</body></html>"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = email
    msg.attach(MIMEText(body_plain, "plain"))
    msg.attach(MIMEText(body_html, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            if SMTP_USER and SMTP_PASSWORD:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, [email], msg.as_string())
        logger.info("Verification email sent to %s", email)
    except Exception as exc:
        logger.error("Failed to send verification email to %s: %s", email, exc)
        logger.info("Verification link for %s (SMTP fallback): %s", email, verify_url)


def verify_email_token(token: str) -> str:
    """Validate the token, mark the user's email as verified. Returns the email."""
    email = _consume_verification_token(token)
    with _users_lock:
        user = _get_user(email)
        if not user:
            raise HTTPException(status_code=404, detail="User account not found")
        if not user.get("email_verified"):
            user["email_verified"] = True
            _upsert_user(user)
    logger.info("Email verified for %s", email)
    return email


def resend_verification(email: str) -> dict:
    """Generate a new verification token and resend the email. Always returns a generic message."""
    _generic = {"message": "If that address is registered and unverified, a new link has been sent."}
    email = email.strip().lower()
    if not email or "@" not in email:
        return _generic
    with _users_lock:
        user = _get_user(email)
    if not user or user.get("is_admin") or user.get("email_verified"):
        return _generic
    token = generate_verification_token(email)
    send_verification_email(email, token, APP_BASE_URL)
    return _generic


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
    _ensure_admin_initialized()
    
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
                existing["email_verified"] = True
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
                email_verified=is_admin,
                created_at=_utcnow_iso(),
            ).model_dump()
        )

    if is_admin:
        return {"message": "Admin account created. You can log in immediately.", "approved": True}

    # Send verification email for non-admin accounts
    token = generate_verification_token(email)
    send_verification_email(email, token, APP_BASE_URL)

    return {
        "message": (
            "Please check your email and click the verification link before your request "
            "is reviewed by an admin."
        ),
        "approved": False,
        "email_verification_sent": True,
    }


def login_user(email: str, password: str) -> TokenResponse:
    _ensure_admin_initialized()
    
    email = email.strip().lower()

    with _users_lock:
        user = _get_user(email)

    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    hashed = user.get("hashed_password", "")
    if not verify_password(password, hashed):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.get("is_admin") and not user.get("email_verified", False):
        raise HTTPException(
            status_code=403,
            detail="Please verify your email address first. Check your inbox for the verification link.",
        )

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


def reject_user(email: str) -> dict:
    email = email.strip().lower()
    if email == ADMIN_EMAIL:
        raise HTTPException(status_code=400, detail="Cannot reject the configured admin account")

    with _users_lock:
        user = _get_user(email)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if user.get("is_approved"):
            raise HTTPException(status_code=400, detail="Approved users cannot be rejected")
        deleted = _delete_user(email)

    if not deleted:
        raise HTTPException(status_code=500, detail="Failed to reject user")
    return {"message": f"{email} has been rejected and removed"}


def revoke_user(email: str) -> dict:
    email = email.strip().lower()
    if email == ADMIN_EMAIL:
        raise HTTPException(status_code=400, detail="Cannot revoke the configured admin account")

    with _users_lock:
        user = _get_user(email)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if user.get("is_admin"):
            raise HTTPException(status_code=400, detail="Cannot revoke an admin account")
        if not user.get("is_approved"):
            return {"message": f"{email} is already not approved"}

        user["is_approved"] = False
        _upsert_user(user)

    return {"message": f"{email} access has been revoked"}


def list_pending_users() -> list[dict]:
    with _users_lock:
        users = _list_users()
    return [
        {
            "email": u["email"],
            "email_verified": u.get("email_verified", False),
            "created_at": u.get("created_at", ""),
        }
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
            "email_verified": u.get("email_verified", False),
            "created_at": u.get("created_at", ""),
        }
        for u in users
    ]


# Lazy initialization flag - admin account creation deferred to first auth request
_admin_initialized = False
_admin_init_lock = threading.Lock()


def _ensure_admin_initialized():
    """Lazy initialization of admin account (called on first auth request)."""
    global _admin_initialized
    if _admin_initialized:
        return
    
    with _admin_init_lock:
        if _admin_initialized:
            return
        
        try:
            _ensure_admin_exists()
            _admin_initialized = True
        except Exception as exc:
            logger.warning("Could not initialize admin account: %s", exc)