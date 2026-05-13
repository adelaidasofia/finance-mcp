"""Diagnostic tools: healthcheck + audit_tail."""

from __future__ import annotations

import os
from typing import Any

from .. import db, keychain, vault_writer
from ..plaid_client import env_summary


def healthcheck() -> dict[str, Any]:
    """Verify Plaid creds present, vault writable, Keychain accessible,
    DB initialized. Returns a green/yellow/red summary.
    """
    env = env_summary()
    vp = vault_writer.vault_path()
    finance_dir = vault_writer.finance_folder()
    aliases = keychain.list_aliases()
    items_count = len(db.list_items())

    checks = {
        "plaid_creds_present": env["creds_present"],
        "plaid_env": env["env"],
        "vault_path_present": vp is not None,
        "vault_path": str(vp) if vp else None,
        "finance_folder_present": finance_dir is not None,
        "finance_folder": str(finance_dir) if finance_dir else None,
        "keychain_aliases_count": len(aliases),
        "keychain_aliases": aliases,
        "sqlite_items_count": items_count,
        "audit_log_path": os.environ.get("FINANCE_MCP_AUDIT_LOG", "audit.log"),
    }

    blockers: list[str] = []
    if not env["creds_present"]:
        blockers.append(
            "PLAID_CLIENT_ID and PLAID_SECRET missing. Copy .env.example to .env "
            "and fill from https://dashboard.plaid.com."
        )
    if not vp:
        blockers.append("Vault path does not exist. Set FINANCE_MCP_VAULT_PATH.")
    if vp and not finance_dir:
        blockers.append(
            "Vault present but Finance folder missing. Either create it at "
            f"{vp}/{os.environ.get('FINANCE_MCP_FINANCE_FOLDER', '🏠 Home/Finance')} "
            "or set FINANCE_MCP_FINANCE_FOLDER to empty to disable vault writes."
        )
    if len(aliases) != items_count:
        blockers.append(
            f"Drift: keychain has {len(aliases)} aliases but SQLite has {items_count} items. "
            "Run reconcile() (TODO) or unlink stale entries."
        )

    return {
        "ok": len(blockers) == 0,
        "status": "green" if not blockers else ("red" if not env["creds_present"] else "yellow"),
        "checks": checks,
        "blockers": blockers,
    }


def audit_tail(n: int = 50) -> dict[str, Any]:
    """Return last N audit rows from SQLite + tail of the text audit log."""
    rows = db.audit_tail(n=n)
    return {
        "ok": True,
        "count": len(rows),
        "rows": rows,
    }
