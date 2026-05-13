"""finance-mcp FastMCP entry point.

Registers all tools. Loads env from .env if present (without overriding env
vars already in the process), then starts the stdio server.

Run via:
    python3 -m finance_mcp.server

Or via the registered console script:
    finance-mcp

Configuration: see .env.example. All env vars are documented there.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from . import __version__  # noqa: F401 — also fires .env loader
from .tools import diag, link, sync

mcp = FastMCP(
    name="finance-mcp",
    version=__version__,
    instructions=(
        "Local-first personal finance MCP. Aggregates accounts via Plaid; writes "
        "balances, holdings, and cash-flow rollups to your local Obsidian-style "
        "markdown vault. Access tokens stay in macOS Keychain — never returned in "
        "tool responses, never logged.\n\n"
        "First-time setup: run `healthcheck` to verify env. Then `link_start("
        "institution_alias='chase')` and follow the browser flow, then "
        "`link_complete(...)` to store the token. Re-run `link_start` per bank.\n\n"
        "Daily use: `sync_balances` writes Accounts.md, `sync_holdings` writes "
        "Investments.md, `sync_transactions` updates the local DB, "
        "`rollup_month(month='YYYY-MM')` writes Cash Flow.md.\n\n"
        "Never exfiltrates data. Plaid is the only outbound network call."
    ),
)


# ---- link / unlink ----


@mcp.tool()
def link_start(institution_alias: str, user_token: str = "") -> dict[str, Any]:
    """Start linking a new institution. Returns a Plaid link_token the user
    completes in their browser, then calls link_complete with the public_token.

    Args:
        institution_alias: A short label for this bank (e.g. "chase", "citi",
            "schwab"). Used as the Keychain account name. Must be unique.
        user_token: Optional persistent Plaid user_token. Pass empty string
            to use the alias as the ephemeral client_user_id.
    """
    return link.link_start(institution_alias, user_token)


@mcp.tool()
def link_complete(institution_alias: str, public_token: str) -> dict[str, Any]:
    """Exchange a public_token (from completed Plaid Link) for a permanent
    access_token. Stores token in macOS Keychain. Caches institution + accounts
    metadata in SQLite.

    Args:
        institution_alias: Same alias used in link_start.
        public_token: The public_token returned by Plaid Link after the user
            completed bank auth in their browser.
    """
    return link.link_complete(institution_alias, public_token)


@mcp.tool()
def list_linked() -> dict[str, Any]:
    """List all linked institutions with their last-sync timestamps and
    keychain status. Does NOT return any access tokens.
    """
    return link.list_linked()


@mcp.tool()
def unlink(institution_alias: str) -> dict[str, Any]:
    """Remove a linked institution. Revokes the Plaid item, deletes the
    keychain entry, and clears local SQLite state.
    """
    return link.unlink(institution_alias)


# ---- sync ----


@mcp.tool()
def sync_balances(institution_alias: str = "") -> dict[str, Any]:
    """Pull current balances for all linked institutions (or one specific
    alias) and write the auto-sync block in Accounts.md.

    Args:
        institution_alias: Pass empty string to sync ALL linked institutions.
            Otherwise sync just the named one.
    """
    return sync.sync_balances(institution_alias)


@mcp.tool()
def sync_holdings(institution_alias: str = "") -> dict[str, Any]:
    """Pull investment holdings (stocks, ETFs, mutual funds, bonds) and write
    the auto-sync block in Investments.md.

    Skipped silently for institutions that don't support the Plaid investments
    product (most checking-only banks).
    """
    return sync.sync_holdings(institution_alias)


@mcp.tool()
def sync_transactions(institution_alias: str = "", days: int = 90) -> dict[str, Any]:
    """Cursor-based transaction sync. First run pulls historical; subsequent
    runs pull only the delta. Stores raw transactions in SQLite. Use
    `rollup_month` to write the Cash Flow.md monthly summary afterward.

    Args:
        institution_alias: Empty string syncs all. Otherwise one specific bank.
        days: Initial-pull window hint (Plaid honors its own default).
    """
    return sync.sync_transactions(institution_alias, days=days)


@mcp.tool()
def sync_liabilities(institution_alias: str = "") -> dict[str, Any]:
    """Pull credit-card APRs, minimums, statement balances, and student-loan
    payoff info. Skipped silently for institutions that don't support the
    Plaid liabilities product.
    """
    return sync.sync_liabilities(institution_alias)


@mcp.tool()
def sync_all(institution_alias: str = "") -> dict[str, Any]:
    """Convenience: balances + holdings + liabilities + transactions in
    sequence. Use this for daily / on-demand full refresh.
    """
    return sync.sync_all(institution_alias)


@mcp.tool()
def rollup_month(month: str) -> dict[str, Any]:
    """Compute income / expense / by-category totals for a given YYYY-MM from
    the SQLite transaction store, then write the auto-sync block in
    Cash Flow.md.

    Args:
        month: Format YYYY-MM (e.g. "2026-05").
    """
    return sync.rollup_month(month)


# ---- diagnostics ----


@mcp.tool()
def healthcheck() -> dict[str, Any]:
    """Verify the MCP's runtime config: Plaid creds, vault path, finance
    folder, keychain access, SQLite state. Returns a green/yellow/red
    summary with explicit blockers if any.
    """
    return diag.healthcheck()


@mcp.tool()
def audit_tail(n: int = 50) -> dict[str, Any]:
    """Return the last N audit-log entries. Every Plaid call and every
    keychain operation is recorded.
    """
    return diag.audit_tail(n=n)


def run() -> None:
    """Console-script entry point."""
    mcp.run()


if __name__ == "__main__":
    run()
