"""Sync tools: pull current state from Plaid, write to vault + SQLite.

Each sync function:
1. Iterates linked institutions (or one specific alias)
2. Calls the relevant Plaid endpoint
3. Updates SQLite (transactions, item rows)
4. Updates vault markdown via vault_writer
5. Records audit log entries
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

from plaid.exceptions import ApiException
from plaid.model.accounts_balance_get_request import AccountsBalanceGetRequest
from plaid.model.investments_holdings_get_request import InvestmentsHoldingsGetRequest
from plaid.model.liabilities_get_request import LiabilitiesGetRequest
from plaid.model.transactions_sync_request import TransactionsSyncRequest

from .. import db, keychain, vault_writer
from ..plaid_client import PlaidConfigError, get_client


def _iter_targets(institution_alias: Optional[str]) -> list[str]:
    items = db.list_items()
    if institution_alias:
        alias = institution_alias.strip().lower()
        return [r["institution_alias"] for r in items if r["institution_alias"] == alias]
    return [r["institution_alias"] for r in items]


def sync_balances(institution_alias: str = "") -> dict[str, Any]:
    """Pull live balances for all (or one) linked institutions. Writes
    the auto-sync block in `Accounts.md`.
    """
    try:
        client = get_client()
    except PlaidConfigError as e:
        return {"ok": False, "error": str(e)}

    aliases = _iter_targets(institution_alias or None)
    if not aliases:
        return {"ok": False, "error": "no linked institutions match"}

    all_accounts: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for alias in aliases:
        token = keychain.get_token(alias)
        if not token:
            errors[alias] = "no access_token in keychain"
            continue
        try:
            resp = client.accounts_balance_get(
                AccountsBalanceGetRequest(access_token=token)
            )
            for a in resp.accounts:
                bal_avail = a.balances.available
                bal_curr = a.balances.current
                iso = a.balances.iso_currency_code or "USD"
                effective = bal_avail if bal_avail is not None else bal_curr
                if effective is None:
                    effective = 0.0
                usd = _to_usd(float(effective), iso)
                all_accounts.append(
                    {
                        "institution_alias": alias,
                        "account_id": a.account_id,
                        "name": a.name,
                        "official_name": getattr(a, "official_name", None) or a.name,
                        "mask": a.mask,
                        "type": str(a.type),
                        "subtype": str(a.subtype) if a.subtype else None,
                        "balance_available": float(bal_avail) if bal_avail is not None else None,
                        "balance_current": float(bal_curr) if bal_curr is not None else None,
                        "iso_currency_code": iso,
                        "balance_usd": usd,
                    }
                )
            db.mark_synced(alias, "balance")
            db.audit_log("sync_balances", alias, success=True, detail=f"accounts={len(resp.accounts)}")
        except ApiException as e:
            errors[alias] = _plaid_err(e)
            db.audit_log("sync_balances", alias, success=False, error=str(e.body))

    written = vault_writer.write_accounts(all_accounts)
    return {
        "ok": True,
        "accounts_synced": len(all_accounts),
        "institutions_synced": len(aliases) - len(errors),
        "errors": errors,
        "vault_file": str(written) if written else None,
    }


def sync_holdings(institution_alias: str = "") -> dict[str, Any]:
    """Pull investment holdings (stocks, ETFs, mutual funds, bonds) and write
    Investments.md auto-sync block.
    """
    try:
        client = get_client()
    except PlaidConfigError as e:
        return {"ok": False, "error": str(e)}

    aliases = _iter_targets(institution_alias or None)
    all_holdings: list[dict[str, Any]] = []
    errors: dict[str, str] = {}

    for alias in aliases:
        token = keychain.get_token(alias)
        if not token:
            errors[alias] = "no access_token in keychain"
            continue
        try:
            resp = client.investments_holdings_get(
                InvestmentsHoldingsGetRequest(access_token=token)
            )
            securities_by_id = {s.security_id: s for s in resp.securities}
            for h in resp.holdings:
                sec = securities_by_id.get(h.security_id)
                ticker = (
                    sec.ticker_symbol if sec and sec.ticker_symbol else (sec.name if sec else "")
                )
                sec_type = str(sec.type) if sec and sec.type else None
                all_holdings.append(
                    {
                        "institution_alias": alias,
                        "account_id": h.account_id,
                        "security_id": h.security_id,
                        "ticker_symbol": ticker,
                        "security_name": sec.name if sec else ticker,
                        "security_type": sec_type,
                        "quantity": float(h.quantity) if h.quantity is not None else 0,
                        "institution_price": float(h.institution_price)
                        if h.institution_price is not None
                        else 0,
                        "institution_value": float(h.institution_value)
                        if h.institution_value is not None
                        else 0,
                        "cost_basis": float(h.cost_basis) if h.cost_basis is not None else None,
                        "iso_currency_code": h.iso_currency_code or "USD",
                    }
                )
            db.mark_synced(alias, "holdings")
            db.audit_log("sync_holdings", alias, success=True, detail=f"holdings={len(resp.holdings)}")
        except ApiException as e:
            # Many institutions don't support investments product — non-fatal
            err_msg = _plaid_err(e)
            if "PRODUCTS_NOT_SUPPORTED" in err_msg or "INVALID_PRODUCT" in err_msg:
                continue
            errors[alias] = err_msg
            db.audit_log("sync_holdings", alias, success=False, error=str(e.body))

    written = vault_writer.write_holdings(all_holdings) if all_holdings else None
    return {
        "ok": True,
        "holdings_synced": len(all_holdings),
        "errors": errors,
        "vault_file": str(written) if written else None,
    }


def sync_transactions(institution_alias: str = "", days: int = 90) -> dict[str, Any]:
    """Cursor-based transaction sync. First run pulls historical; subsequent
    runs pull only delta. Writes transactions to SQLite.
    """
    try:
        client = get_client()
    except PlaidConfigError as e:
        return {"ok": False, "error": str(e)}

    aliases = _iter_targets(institution_alias or None)
    totals = {"added": 0, "modified": 0, "removed": 0}
    errors: dict[str, str] = {}

    for alias in aliases:
        token = keychain.get_token(alias)
        if not token:
            errors[alias] = "no access_token in keychain"
            continue
        item = db.get_item(alias) or {}
        cursor = item.get("transactions_cursor")

        has_more = True
        rows_to_insert: list[dict[str, Any]] = []
        try:
            while has_more:
                req = TransactionsSyncRequest(
                    access_token=token,
                    cursor=cursor or "",
                )
                resp = client.transactions_sync(req)
                for t in resp.added + resp.modified:
                    rows_to_insert.append(
                        {
                            "plaid_tx_id": t.transaction_id,
                            "institution_alias": alias,
                            "account_id": t.account_id,
                            "posted_date": str(t.date),
                            "amount": float(t.amount) if t.amount is not None else 0.0,
                            "iso_currency": t.iso_currency_code or "USD",
                            "merchant_name": getattr(t, "merchant_name", None) or "",
                            "name": t.name or "",
                            "primary_category": _primary_cat(t),
                            "detailed_category": _detailed_cat(t),
                            "pending": bool(t.pending),
                            "payment_channel": str(t.payment_channel) if t.payment_channel else None,
                            "raw_json": json.dumps(_tx_to_dict(t), default=str),
                        }
                    )
                totals["added"] += len(resp.added)
                totals["modified"] += len(resp.modified)
                totals["removed"] += len(resp.removed)
                cursor = resp.next_cursor
                has_more = resp.has_more
            db.insert_transactions(rows_to_insert)
            db.set_tx_cursor(alias, cursor)
            db.mark_synced(alias, "tx")
            db.audit_log(
                "sync_transactions", alias,
                success=True,
                detail=f"added={len(rows_to_insert)}",
            )
        except ApiException as e:
            errors[alias] = _plaid_err(e)
            db.audit_log("sync_transactions", alias, success=False, error=str(e.body))

    return {
        "ok": True,
        "totals": totals,
        "errors": errors,
        "next_step": "Call rollup_month(month='YYYY-MM') to write the Cash Flow.md summary.",
    }


def sync_liabilities(institution_alias: str = "") -> dict[str, Any]:
    """Pull credit-card APRs + minimums + statement balances and student-loan
    payoff info. Writes notes into the audit log; future versions will write
    a dedicated Liabilities.md block.
    """
    try:
        client = get_client()
    except PlaidConfigError as e:
        return {"ok": False, "error": str(e)}

    aliases = _iter_targets(institution_alias or None)
    summary: list[dict[str, Any]] = []
    errors: dict[str, str] = {}

    for alias in aliases:
        token = keychain.get_token(alias)
        if not token:
            errors[alias] = "no access_token in keychain"
            continue
        try:
            resp = client.liabilities_get(LiabilitiesGetRequest(access_token=token))
            for cc in resp.liabilities.credit or []:
                summary.append(
                    {
                        "institution_alias": alias,
                        "kind": "credit",
                        "account_id": cc.account_id,
                        "minimum_payment_amount": float(cc.minimum_payment_amount)
                        if cc.minimum_payment_amount is not None else None,
                        "next_payment_due_date": str(cc.next_payment_due_date)
                        if cc.next_payment_due_date else None,
                        "last_statement_balance": float(cc.last_statement_balance)
                        if cc.last_statement_balance is not None else None,
                    }
                )
            for st in resp.liabilities.student or []:
                summary.append(
                    {
                        "institution_alias": alias,
                        "kind": "student_loan",
                        "account_id": st.account_id,
                        "loan_name": st.loan_name,
                        "outstanding_interest_amount": float(st.outstanding_interest_amount)
                        if st.outstanding_interest_amount is not None else None,
                        "interest_rate_percentage": float(st.interest_rate_percentage)
                        if st.interest_rate_percentage is not None else None,
                        "next_payment_due_date": str(st.next_payment_due_date)
                        if st.next_payment_due_date else None,
                    }
                )
            db.mark_synced(alias, "liab")
            db.audit_log("sync_liabilities", alias, success=True, detail=f"rows={len(summary)}")
        except ApiException as e:
            err_msg = _plaid_err(e)
            if "PRODUCTS_NOT_SUPPORTED" in err_msg:
                continue
            errors[alias] = err_msg
            db.audit_log("sync_liabilities", alias, success=False, error=str(e.body))

    return {"ok": True, "liabilities": summary, "errors": errors}


def sync_all(institution_alias: str = "") -> dict[str, Any]:
    """Convenience: balances + holdings + liabilities + transactions in sequence."""
    return {
        "balances": sync_balances(institution_alias),
        "holdings": sync_holdings(institution_alias),
        "liabilities": sync_liabilities(institution_alias),
        "transactions": sync_transactions(institution_alias),
    }


def rollup_month(month: str) -> dict[str, Any]:
    """Compute income / expense / by-category totals for a given YYYY-MM from
    the SQLite transaction store, then write Cash Flow.md auto-block.
    """
    if not _valid_month(month):
        return {"ok": False, "error": "month must be YYYY-MM"}
    year, mo = month.split("-")
    start = f"{year}-{mo}-01"
    end = f"{year}-{mo}-31"
    rows = db.transactions_between(start, end)
    income = 0.0
    expenses = 0.0
    by_cat: dict[str, float] = defaultdict(float)
    for r in rows:
        amt = float(r["amount"])
        # Plaid: positive = outflow (debit), negative = inflow (credit). We invert for clarity.
        if amt < 0:
            income += abs(amt)
        else:
            expenses += amt
        cat = r["primary_category"] or "uncategorized"
        by_cat[cat] += amt
    written = vault_writer.write_cash_flow_summary(month, income, expenses, dict(by_cat))
    return {
        "ok": True,
        "month": month,
        "transactions_in_window": len(rows),
        "income_usd": round(income, 2),
        "expenses_usd": round(expenses, 2),
        "net_usd": round(income - expenses, 2),
        "vault_file": str(written) if written else None,
    }


# ---- helpers ----


def _to_usd(amount: float, iso: str) -> float:
    """Naive FX. Replace with live rate when needed."""
    if iso == "USD":
        return amount
    rates = {"COP": 1 / 4100, "EUR": 1.08, "GBP": 1.25, "CAD": 0.73, "MXN": 0.058}
    return amount * rates.get(iso, 1.0)


def _primary_cat(tx) -> str:
    pcat = getattr(tx, "personal_finance_category", None)
    if pcat and getattr(pcat, "primary", None):
        return str(pcat.primary)
    cats = getattr(tx, "category", None) or []
    return cats[0] if cats else ""


def _detailed_cat(tx) -> str:
    pcat = getattr(tx, "personal_finance_category", None)
    if pcat and getattr(pcat, "detailed", None):
        return str(pcat.detailed)
    cats = getattr(tx, "category", None) or []
    return cats[-1] if cats else ""


def _tx_to_dict(tx) -> dict[str, Any]:
    """Flatten Plaid transaction model to JSON-safe dict for raw_json column."""
    return {
        "transaction_id": tx.transaction_id,
        "account_id": tx.account_id,
        "date": str(tx.date),
        "amount": tx.amount,
        "iso_currency_code": tx.iso_currency_code,
        "name": tx.name,
        "merchant_name": getattr(tx, "merchant_name", None),
        "category": list(getattr(tx, "category", None) or []),
        "primary_category": _primary_cat(tx),
        "detailed_category": _detailed_cat(tx),
        "pending": tx.pending,
        "payment_channel": str(tx.payment_channel) if tx.payment_channel else None,
    }


def _plaid_err(e: ApiException) -> str:
    try:
        body = json.loads(e.body) if e.body else {}
        return body.get("error_code") or body.get("error_message") or str(e)
    except Exception:
        return str(e)


def _valid_month(s: str) -> bool:
    try:
        datetime.strptime(s, "%Y-%m")
        return True
    except ValueError:
        return False
