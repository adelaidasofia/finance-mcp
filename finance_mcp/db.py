"""SQLite for transaction history + sync cursors + audit log.

NOT a source of truth — vault Markdown is the source of truth for balances,
holdings, and monthly cash-flow rollups. SQLite holds:

- `items`: linked institution metadata (alias, institution_id, accounts list,
  last sync timestamps, cursor for transactions/sync)
- `transactions`: full transaction history for categorization + monthly rollup
- `audit`: every Plaid call and every Keychain access for forensic review

Path: env FINANCE_MCP_DB_PATH or ./data.db relative to MCP folder.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

DEFAULT_DB_NAME = "data.db"


def _resolve_db_path() -> Path:
    raw = os.environ.get("FINANCE_MCP_DB_PATH", "").strip()
    if not raw:
        here = Path(__file__).resolve().parent.parent
        return here / DEFAULT_DB_NAME
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p
    here = Path(__file__).resolve().parent.parent
    return here / p


SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    institution_alias TEXT PRIMARY KEY,
    institution_id    TEXT,
    institution_name  TEXT,
    plaid_item_id     TEXT,
    accounts_json     TEXT,
    transactions_cursor TEXT,
    last_balance_sync  TEXT,
    last_holdings_sync TEXT,
    last_tx_sync       TEXT,
    last_liab_sync     TEXT,
    created_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    plaid_tx_id       TEXT PRIMARY KEY,
    institution_alias TEXT NOT NULL,
    account_id        TEXT NOT NULL,
    posted_date       TEXT NOT NULL,
    amount            REAL NOT NULL,
    iso_currency      TEXT,
    merchant_name     TEXT,
    name              TEXT,
    primary_category  TEXT,
    detailed_category TEXT,
    pending           INTEGER NOT NULL DEFAULT 0,
    payment_channel   TEXT,
    raw_json          TEXT NOT NULL,
    inserted_at       TEXT NOT NULL,
    FOREIGN KEY (institution_alias) REFERENCES items(institution_alias)
);
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(posted_date DESC);
CREATE INDEX IF NOT EXISTS idx_tx_inst ON transactions(institution_alias);

CREATE TABLE IF NOT EXISTS audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    event       TEXT NOT NULL,
    detail      TEXT,
    success     INTEGER NOT NULL,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts DESC);
"""


def _connect(path: Optional[Path] = None) -> sqlite3.Connection:
    p = path or _resolve_db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def upsert_item(
    institution_alias: str,
    institution_id: Optional[str],
    institution_name: Optional[str],
    plaid_item_id: Optional[str],
    accounts: Optional[list[dict[str, Any]]] = None,
) -> None:
    with session() as conn:
        conn.execute(
            """
            INSERT INTO items (institution_alias, institution_id, institution_name,
                               plaid_item_id, accounts_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(institution_alias) DO UPDATE SET
                institution_id   = excluded.institution_id,
                institution_name = excluded.institution_name,
                plaid_item_id    = excluded.plaid_item_id,
                accounts_json    = COALESCE(excluded.accounts_json, items.accounts_json)
            """,
            (
                institution_alias,
                institution_id,
                institution_name,
                plaid_item_id,
                json.dumps(accounts) if accounts is not None else None,
                now_iso(),
            ),
        )


def get_item(institution_alias: str) -> Optional[dict[str, Any]]:
    with session() as conn:
        row = conn.execute(
            "SELECT * FROM items WHERE institution_alias = ?", (institution_alias,)
        ).fetchone()
        if not row:
            return None
        return dict(row)


def list_items() -> list[dict[str, Any]]:
    with session() as conn:
        rows = conn.execute(
            "SELECT * FROM items ORDER BY institution_alias"
        ).fetchall()
        return [dict(r) for r in rows]


def delete_item(institution_alias: str) -> int:
    with session() as conn:
        cur = conn.execute(
            "DELETE FROM items WHERE institution_alias = ?", (institution_alias,)
        )
        conn.execute(
            "DELETE FROM transactions WHERE institution_alias = ?", (institution_alias,)
        )
        return cur.rowcount


def mark_synced(institution_alias: str, kind: str) -> None:
    """kind in {'balance','holdings','tx','liab'}"""
    column = {
        "balance": "last_balance_sync",
        "holdings": "last_holdings_sync",
        "tx": "last_tx_sync",
        "liab": "last_liab_sync",
    }.get(kind)
    if not column:
        return
    with session() as conn:
        conn.execute(
            f"UPDATE items SET {column} = ? WHERE institution_alias = ?",
            (now_iso(), institution_alias),
        )


def set_tx_cursor(institution_alias: str, cursor: Optional[str]) -> None:
    with session() as conn:
        conn.execute(
            "UPDATE items SET transactions_cursor = ? WHERE institution_alias = ?",
            (cursor, institution_alias),
        )


def insert_transactions(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    inserted = 0
    with session() as conn:
        for r in rows:
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO transactions
                    (plaid_tx_id, institution_alias, account_id, posted_date, amount,
                     iso_currency, merchant_name, name, primary_category,
                     detailed_category, pending, payment_channel, raw_json, inserted_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        r["plaid_tx_id"],
                        r["institution_alias"],
                        r["account_id"],
                        r["posted_date"],
                        r["amount"],
                        r.get("iso_currency"),
                        r.get("merchant_name"),
                        r.get("name"),
                        r.get("primary_category"),
                        r.get("detailed_category"),
                        1 if r.get("pending") else 0,
                        r.get("payment_channel"),
                        r["raw_json"],
                        now_iso(),
                    ),
                )
                inserted += 1
            except sqlite3.Error:
                continue
    return inserted


def transactions_between(start_date: str, end_date: str) -> list[dict[str, Any]]:
    with session() as conn:
        rows = conn.execute(
            """
            SELECT * FROM transactions
            WHERE posted_date BETWEEN ? AND ?
            ORDER BY posted_date DESC
            """,
            (start_date, end_date),
        ).fetchall()
        return [dict(r) for r in rows]


def audit_log(event: str, detail: str = "", success: bool = True, error: str = "") -> None:
    """Persist an audit row AND append to text log for tail-readability."""
    with session() as conn:
        conn.execute(
            "INSERT INTO audit (ts, event, detail, success, error) VALUES (?,?,?,?,?)",
            (now_iso(), event, detail, 1 if success else 0, error or None),
        )
    # also append to text file
    log_path = os.environ.get("FINANCE_MCP_AUDIT_LOG", "").strip() or "audit.log"
    p = Path(log_path)
    if not p.is_absolute():
        p = Path(__file__).resolve().parent.parent / p
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(
                f"{now_iso()}\t{event}\tok={success}\t{detail}\t{error}\n"
            )
    except OSError:
        pass


def audit_tail(n: int = 50) -> list[dict[str, Any]]:
    with session() as conn:
        rows = conn.execute(
            "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        return [dict(r) for r in rows]
