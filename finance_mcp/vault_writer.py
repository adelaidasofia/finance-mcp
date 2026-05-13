"""Vault writers for finance-mcp.

Writes balances, holdings, and cash-flow data to your local markdown vault
using inline-field schema. Compatible with Obsidian + Dataview but works for
any markdown reader.

Vault path: env FINANCE_MCP_VAULT_PATH (required; no default).
Finance folder: env FINANCE_MCP_FINANCE_FOLDER (default 'Finance').
Set folder to empty string to disable vault writes (data stays in SQLite only).

Files touched:
- Accounts.md            (replaces "auto-sync" block, keeps manual block intact)
- Investments.md         (auto-sync block)
- Cash Flow.md           (per-month auto-sync block)
- Equity Stakes.md       (NOT touched — equity stakes are manual marking)
- Net Worth.md           (NOT touched — monthly snapshot is a manual ritual)

Convention: every auto-written block is delimited by HTML comments:

    <!-- finance-mcp:BEGIN -->
    ...auto content...
    <!-- finance-mcp:END -->

Manual content outside these markers is preserved.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

BEGIN_MARKER = "<!-- finance-mcp:BEGIN -->"
END_MARKER = "<!-- finance-mcp:END -->"


def vault_path() -> Optional[Path]:
    raw = os.environ.get("FINANCE_MCP_VAULT_PATH", "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    return p if p.exists() else None


def finance_folder() -> Optional[Path]:
    """Returns the Finance folder Path, or None if writes are disabled."""
    folder = os.environ.get("FINANCE_MCP_FINANCE_FOLDER", "Finance")
    if not folder.strip():
        return None
    vp = vault_path()
    if not vp:
        return None
    full = vp / folder
    return full if full.exists() else None


def writes_enabled() -> bool:
    return finance_folder() is not None


def _now_iso_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _replace_auto_block(file_path: Path, new_block: str) -> None:
    """Replace content between BEGIN/END markers, or append the block.

    Preserves all manual content outside the markers. Idempotent.
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    if not file_path.exists():
        file_path.write_text(new_block + "\n")
        return
    existing = file_path.read_text()
    pattern = re.compile(
        re.escape(BEGIN_MARKER) + r".*?" + re.escape(END_MARKER),
        re.DOTALL,
    )
    if pattern.search(existing):
        replaced = pattern.sub(new_block, existing)
    else:
        replaced = existing.rstrip() + "\n\n" + new_block + "\n"
    file_path.write_text(replaced)


def _auto_block(body_lines: list[str]) -> str:
    header = (
        f"{BEGIN_MARKER}\n"
        f"<!-- last_sync: {datetime.now(timezone.utc).isoformat(timespec='seconds')} -->\n"
        f"<!-- source: finance-mcp v0.1.0 (Plaid). Edit the source, not the block. -->\n"
    )
    return header + "\n".join(body_lines) + "\n" + END_MARKER


def write_accounts(accounts: list[dict[str, Any]]) -> Optional[Path]:
    """Write the auto-sync block to Accounts.md.

    Each account: institution alias, mask, type, balance (native + USD),
    last verified. Always emits inline fields Dataview can read.

    Returns the file path written, or None if vault writes are disabled.
    """
    folder = finance_folder()
    if not folder:
        return None
    file_path = folder / "Accounts.md"
    body: list[str] = ["## Auto-synced accounts", ""]
    for a in accounts:
        alias = a.get("institution_alias") or "unknown"
        mask = a.get("mask") or "----"
        name = a.get("name") or "account"
        official = a.get("official_name") or name
        atype = a.get("type") or "cash"
        subtype = a.get("subtype") or ""
        currency = a.get("iso_currency_code") or "USD"
        balance = a.get("balance_available")
        if balance is None:
            balance = a.get("balance_current")
        balance = balance if balance is not None else 0.0
        usd = a.get("balance_usd") if a.get("balance_usd") is not None else balance
        liquidity = _liquidity_for(atype, subtype)
        body.append(f"### {alias} — {official} ({mask})")
        body.append("")
        body.append(f"`subtype:: account`")
        body.append(f"`account_name:: {official} ({mask})`")
        body.append(f"`institution_alias:: {alias}`")
        body.append(f"`account_type:: {_normalize_type(atype, subtype)}`")
        body.append(f"`account_subtype:: {subtype}`")
        body.append(f"`balance:: {balance:.2f}`")
        body.append(f"`balance_currency:: {currency}`")
        body.append(f"`balance_usd:: {usd:.2f}`")
        body.append(f"`liquidity:: {liquidity}`")
        body.append(f"`last_verified:: {_now_iso_date()}`")
        body.append("")
    block = _auto_block(body)
    _replace_auto_block(file_path, block)
    return file_path


def write_holdings(holdings: list[dict[str, Any]]) -> Optional[Path]:
    """Write the auto-sync block to Investments.md."""
    folder = finance_folder()
    if not folder:
        return None
    file_path = folder / "Investments.md"
    body: list[str] = ["## Auto-synced holdings", ""]
    for h in holdings:
        alias = h.get("institution_alias") or "unknown"
        ticker = h.get("ticker_symbol") or h.get("security_name") or "?"
        security_name = h.get("security_name") or ticker
        quantity = h.get("quantity") or 0
        price = h.get("institution_price") or 0
        value = h.get("institution_value") or quantity * price
        cost = h.get("cost_basis")
        currency = h.get("iso_currency_code") or "USD"
        asset_class = _asset_class_for(h.get("security_type"))
        body.append(f"### {ticker} ({alias})")
        body.append("")
        body.append(f"`subtype:: investment`")
        body.append(f"`holding:: {ticker}`")
        body.append(f"`security_name:: {security_name}`")
        body.append(f"`asset_class:: {asset_class}`")
        body.append(f"`institution_alias:: {alias}`")
        body.append(f"`shares:: {quantity}`")
        body.append(f"`price_usd:: {price}`")
        body.append(f"`value_usd:: {value:.2f}`")
        if cost is not None:
            body.append(f"`cost_basis_usd:: {cost:.2f}`")
            if cost > 0:
                ret = (value - cost) / cost * 100
                body.append(f"`return_pct:: {ret:.2f}`")
        body.append(f"`liquidity:: liquid`")
        body.append(f"`tax_treatment:: taxable`")
        body.append(f"`last_priced:: {_now_iso_date()}`")
        body.append("")
    block = _auto_block(body)
    _replace_auto_block(file_path, block)
    return file_path


def write_cash_flow_summary(
    month: str, income_usd: float, expenses_usd: float, by_category: dict[str, float]
) -> Optional[Path]:
    """Append/update a single-month summary block in Cash Flow.md.

    `month` format: YYYY-MM. Writes inline-field block that Dataview reads.
    """
    folder = finance_folder()
    if not folder:
        return None
    file_path = folder / "Cash Flow.md"
    net = income_usd - expenses_usd
    savings_rate = (net / income_usd * 100) if income_usd > 0 else 0.0
    body: list[str] = [
        f"## {month} (auto)",
        "",
        f"`subtype:: cash_flow_month`",
        f"`month:: {month}`",
        f"`income_usd:: {income_usd:.2f}`",
        f"`expenses_usd:: {expenses_usd:.2f}`",
        f"`net_usd:: {net:.2f}`",
        f"`savings_rate:: {savings_rate:.2f}`",
        "",
        "**By category:**",
        "",
    ]
    for cat, amt in sorted(by_category.items(), key=lambda kv: -abs(kv[1])):
        body.append(f"- {cat}: ${amt:.2f}")
    body.append("")
    block = _auto_block(body)
    _replace_auto_block(file_path, block)
    return file_path


def _normalize_type(atype: str, subtype: str) -> str:
    """Map Plaid type/subtype → vault account_type taxonomy."""
    t = (atype or "").lower()
    s = (subtype or "").lower()
    if t == "depository":
        return "cash"
    if t == "credit":
        return "credit"
    if t == "loan":
        return "credit"
    if t == "investment":
        if "401" in s or "ira" in s or "hsa" in s or "retirement" in s:
            return "retirement"
        return "brokerage"
    if t == "other":
        return "other"
    return t or "other"


def _liquidity_for(atype: str, subtype: str) -> str:
    t = (atype or "").lower()
    s = (subtype or "").lower()
    if t == "investment" and ("401" in s or "ira" in s or "hsa" in s or "retirement" in s):
        return "semi-liquid"
    return "liquid"


def _asset_class_for(security_type: Optional[str]) -> str:
    if not security_type:
        return "other"
    st = security_type.lower()
    if "etf" in st:
        return "etf"
    if "mutual fund" in st or "mutual_fund" in st:
        return "etf"  # treat as index-ish for IPS bucketing
    if "equity" in st:
        return "equity"
    if "fixed income" in st or "bond" in st:
        return "bond"
    if "cash" in st or "money market" in st:
        return "cash"
    if "crypto" in st or "digital" in st:
        return "crypto"
    return "other"
