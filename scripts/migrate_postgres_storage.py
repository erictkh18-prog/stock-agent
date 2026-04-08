"""Migrate Stock Agent persistence tables between two Postgres databases.

Use this to move data from one managed Postgres provider to another
(e.g., Supabase -> Neon) without changing application code.

Tables copied:
- kb_users
- kb_email_tokens
- paper_positions
- paper_closed_trades

Usage:
  python scripts/migrate_postgres_storage.py \
      --source-url "$SOURCE_DATABASE_URL" \
      --target-url "$TARGET_DATABASE_URL"

Or set environment variables:
  SOURCE_DATABASE_URL=...
  TARGET_DATABASE_URL=...
  python scripts/migrate_postgres_storage.py
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from typing import Any

try:
    import psycopg
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "psycopg is required. Install dependencies with: pip install -r requirements.txt"
    ) from exc


@dataclass
class CopyResult:
    table: str
    copied: int
    skipped: bool = False
    reason: str = ""


def _connect(url: str):
    return psycopg.connect(url, connect_timeout=15, autocommit=False)


def _table_exists(conn, table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = %s
            )
            """,
            (table_name,),
        )
        row = cur.fetchone()
        return bool(row[0]) if row else False


def _ensure_target_schema(conn) -> None:
    with conn.cursor() as cur:
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
            CREATE TABLE IF NOT EXISTS paper_positions (
                id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                payload JSONB NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_closed_trades (
                id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                closed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                payload JSONB NOT NULL
            )
            """
        )


def _copy_kb_users(src_conn, dst_conn) -> CopyResult:
    table = "kb_users"
    if not _table_exists(src_conn, table):
        return CopyResult(table=table, copied=0, skipped=True, reason="source table missing")

    with src_conn.cursor() as s_cur, dst_conn.cursor() as d_cur:
        s_cur.execute(
            """
            SELECT email, hashed_password, is_admin, is_approved, created_at
            FROM kb_users
            """
        )
        rows = s_cur.fetchall()
        for row in rows:
            d_cur.execute(
                """
                INSERT INTO kb_users (email, hashed_password, is_admin, is_approved, created_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (email) DO UPDATE SET
                    hashed_password = EXCLUDED.hashed_password,
                    is_admin = EXCLUDED.is_admin,
                    is_approved = EXCLUDED.is_approved,
                    created_at = EXCLUDED.created_at
                """,
                row,
            )
    return CopyResult(table=table, copied=len(rows))


def _copy_kb_email_tokens(src_conn, dst_conn) -> CopyResult:
    table = "kb_email_tokens"
    if not _table_exists(src_conn, table):
        return CopyResult(table=table, copied=0, skipped=True, reason="source table missing")

    with src_conn.cursor() as s_cur, dst_conn.cursor() as d_cur:
        s_cur.execute(
            """
            SELECT token, email, expires_at, created_at
            FROM kb_email_tokens
            """
        )
        rows = s_cur.fetchall()
        for row in rows:
            d_cur.execute(
                """
                INSERT INTO kb_email_tokens (token, email, expires_at, created_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (token) DO UPDATE SET
                    email = EXCLUDED.email,
                    expires_at = EXCLUDED.expires_at,
                    created_at = EXCLUDED.created_at
                """,
                row,
            )
    return CopyResult(table=table, copied=len(rows))


def _copy_paper_positions(src_conn, dst_conn) -> CopyResult:
    table = "paper_positions"
    if not _table_exists(src_conn, table):
        return CopyResult(table=table, copied=0, skipped=True, reason="source table missing")

    with src_conn.cursor() as s_cur, dst_conn.cursor() as d_cur:
        s_cur.execute(
            """
            SELECT id, symbol, opened_at, payload
            FROM paper_positions
            """
        )
        rows = s_cur.fetchall()
        for row in rows:
            d_cur.execute(
                """
                INSERT INTO paper_positions (id, symbol, opened_at, payload)
                VALUES (%s, %s, %s, %s::jsonb)
                ON CONFLICT (id) DO UPDATE SET
                    symbol = EXCLUDED.symbol,
                    opened_at = EXCLUDED.opened_at,
                    payload = EXCLUDED.payload
                """,
                (row[0], row[1], row[2], json.dumps(row[3])),
            )
    return CopyResult(table=table, copied=len(rows))


def _copy_paper_closed_trades(src_conn, dst_conn) -> CopyResult:
    table = "paper_closed_trades"
    if not _table_exists(src_conn, table):
        return CopyResult(table=table, copied=0, skipped=True, reason="source table missing")

    with src_conn.cursor() as s_cur, dst_conn.cursor() as d_cur:
        s_cur.execute(
            """
            SELECT id, symbol, closed_at, payload
            FROM paper_closed_trades
            """
        )
        rows = s_cur.fetchall()
        for row in rows:
            d_cur.execute(
                """
                INSERT INTO paper_closed_trades (id, symbol, closed_at, payload)
                VALUES (%s, %s, %s, %s::jsonb)
                ON CONFLICT (id) DO UPDATE SET
                    symbol = EXCLUDED.symbol,
                    closed_at = EXCLUDED.closed_at,
                    payload = EXCLUDED.payload
                """,
                (row[0], row[1], row[2], json.dumps(row[3])),
            )
    return CopyResult(table=table, copied=len(rows))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate Stock Agent Postgres storage tables")
    parser.add_argument(
        "--source-url",
        default=os.getenv("SOURCE_DATABASE_URL", "").strip(),
        help="Source Postgres URL (or SOURCE_DATABASE_URL env var)",
    )
    parser.add_argument(
        "--target-url",
        default=os.getenv("TARGET_DATABASE_URL", "").strip(),
        help="Target Postgres URL (or TARGET_DATABASE_URL env var)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate connectivity and table presence without writing rows",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.source_url or not args.target_url:
        print("ERROR: both --source-url and --target-url are required (or env vars).")
        return 2

    with _connect(args.source_url) as src_conn, _connect(args.target_url) as dst_conn:
        _ensure_target_schema(dst_conn)
        if args.dry_run:
            src_tables = [
                "kb_users",
                "kb_email_tokens",
                "paper_positions",
                "paper_closed_trades",
            ]
            print("Dry run: connection OK")
            for table in src_tables:
                exists = _table_exists(src_conn, table)
                print(f"- {table}: {'present' if exists else 'missing'}")
            dst_conn.rollback()
            return 0

        results = [
            _copy_kb_users(src_conn, dst_conn),
            _copy_kb_email_tokens(src_conn, dst_conn),
            _copy_paper_positions(src_conn, dst_conn),
            _copy_paper_closed_trades(src_conn, dst_conn),
        ]
        dst_conn.commit()

    print("Migration complete.")
    total = 0
    for item in results:
        if item.skipped:
            print(f"- {item.table}: skipped ({item.reason})")
            continue
        print(f"- {item.table}: copied {item.copied} row(s)")
        total += item.copied
    print(f"Total copied rows: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
