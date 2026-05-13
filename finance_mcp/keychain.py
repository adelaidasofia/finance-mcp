"""macOS Keychain wrapper for Plaid access_token storage.

Access tokens never live in env files or git. The `security` CLI is the only
persistent store. Item refs (institution_alias) are public; the token itself
is fetched only at API-call time.

Service name: "finance-mcp"
Account name: <institution_alias> (e.g. "chase", "citi", "schwab")
Password:     the Plaid access_token

This module is macOS-only. Linux/Windows users override KeychainStore via the
FINANCE_MCP_KEYCHAIN_BACKEND env var (not implemented in v0.1.0).
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from typing import Optional

SERVICE = "finance-mcp"


@dataclass(frozen=True)
class StoreResult:
    ok: bool
    error: Optional[str] = None


def _run(args: list[str], input_str: Optional[str] = None) -> tuple[int, str, str]:
    proc = subprocess.run(
        args,
        input=input_str,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def put_token(institution_alias: str, access_token: str) -> StoreResult:
    """Store or update a Plaid access_token under the given institution alias.

    Overwrites silently if the entry exists. Idempotent.
    """
    if not institution_alias or not access_token:
        return StoreResult(ok=False, error="empty alias or token")
    args = [
        "security", "add-generic-password",
        "-s", SERVICE,
        "-a", institution_alias,
        "-w", access_token,
        "-U",
    ]
    rc, _out, err = _run(args)
    if rc != 0:
        return StoreResult(ok=False, error=err or f"security exited {rc}")
    return StoreResult(ok=True)


def get_token(institution_alias: str) -> Optional[str]:
    """Fetch a Plaid access_token. Returns None if not found.

    Never logs or returns the token elsewhere. Caller passes it directly to
    the Plaid client and discards.
    """
    args = [
        "security", "find-generic-password",
        "-s", SERVICE,
        "-a", institution_alias,
        "-w",
    ]
    rc, out, _err = _run(args)
    if rc != 0:
        return None
    return out


def delete_token(institution_alias: str) -> StoreResult:
    """Remove a stored token. Idempotent: no-op if not present."""
    args = [
        "security", "delete-generic-password",
        "-s", SERVICE,
        "-a", institution_alias,
    ]
    rc, _out, err = _run(args)
    if rc != 0 and "could not be found" not in err.lower():
        return StoreResult(ok=False, error=err or f"security exited {rc}")
    return StoreResult(ok=True)


def list_aliases() -> list[str]:
    """List all institution aliases this MCP has stored tokens for.

    Uses `security dump-keychain` and greps for our service. Slow on large
    keychains; cache the result if you call repeatedly.
    """
    rc, out, _err = _run(["security", "dump-keychain"])
    if rc != 0:
        return []
    aliases: list[str] = []
    current_service: Optional[str] = None
    current_account: Optional[str] = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith('"svce"'):
            current_service = _extract_quoted(line)
        elif line.startswith('"acct"'):
            current_account = _extract_quoted(line)
        elif line.startswith("keychain:"):
            if current_service == SERVICE and current_account:
                aliases.append(current_account)
            current_service = None
            current_account = None
    if current_service == SERVICE and current_account:
        aliases.append(current_account)
    return sorted(set(aliases))


def _extract_quoted(line: str) -> Optional[str]:
    parts = line.split("=", 1)
    if len(parts) != 2:
        return None
    value = parts[1].strip()
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.startswith('<NULL>'):
        return None
    return value
