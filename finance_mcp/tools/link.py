"""Linking tools: connect a new institution, list linked, remove.

Flow:
1. `link_start(institution_alias)` → returns link_token + browser URL
2. User opens URL, completes Plaid Link auth in browser, copies the
   public_token from the success page
3. `link_complete(institution_alias, public_token)` → exchanges for
   permanent access_token, stores in Keychain, returns confirmation

Aliases are user-chosen strings ("chase", "citi", "schwab"). The Plaid
access_token never leaves the Keychain after step 3.
"""

from __future__ import annotations

from typing import Any

from plaid.exceptions import ApiException
from plaid.model.country_code import CountryCode
from plaid.model.item_public_token_exchange_request import (
    ItemPublicTokenExchangeRequest,
)
from plaid.model.item_remove_request import ItemRemoveRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products

from .. import db, keychain
from ..plaid_client import (
    DEFAULT_COUNTRIES,
    DEFAULT_PRODUCTS,
    PlaidConfigError,
    get_client,
)


def link_start(institution_alias: str, user_token: str = "") -> dict[str, Any]:
    """Create a Plaid Link token. User completes auth in browser, then calls
    link_complete with the resulting public_token.

    Args:
        institution_alias: A short label for this bank (e.g. "chase"). Used
            as the Keychain account name and the vault Accounts.md alias.
            Must be unique across already-linked institutions.
        user_token: Optional Plaid user_token if you've already created a
            persistent Plaid user. Pass empty string to use the alias as the
            ephemeral client_user_id.

    Returns:
        dict with link_token, expiration, and instructions URL.
    """
    if not institution_alias or not institution_alias.strip():
        return {"ok": False, "error": "institution_alias required"}
    alias = institution_alias.strip().lower()
    existing = db.get_item(alias)
    if existing and keychain.get_token(alias):
        return {
            "ok": False,
            "error": (
                f"alias '{alias}' is already linked. Use a different alias, or "
                f"call unlink({alias!r}) first."
            ),
        }
    try:
        client = get_client()
    except PlaidConfigError as e:
        return {"ok": False, "error": str(e)}

    req = LinkTokenCreateRequest(
        user=LinkTokenCreateRequestUser(
            client_user_id=user_token or alias,
        ),
        client_name="finance-mcp",
        products=[Products(p) for p in DEFAULT_PRODUCTS],
        country_codes=[CountryCode(c) for c in DEFAULT_COUNTRIES],
        language="en",
    )
    try:
        resp = client.link_token_create(req)
    except ApiException as e:
        db.audit_log("link_start", alias, success=False, error=str(e.body))
        return {"ok": False, "error": _plaid_err(e)}
    db.audit_log("link_start", alias, success=True)
    return {
        "ok": True,
        "institution_alias": alias,
        "link_token": resp.link_token,
        "expiration": str(resp.expiration),
        "next_step": (
            "Open Plaid Link in your browser. Either: (a) use Plaid's hosted "
            "quickstart at https://plaid.com/docs/quickstart/ with this "
            "link_token, OR (b) start the bundled `python3 -m finance_mcp.link_helper "
            f"--link-token {resp.link_token}` to launch a local Link page. "
            "After completing bank auth, copy the public_token and call "
            f"link_complete(institution_alias={alias!r}, public_token=...)."
        ),
    }


def link_complete(institution_alias: str, public_token: str) -> dict[str, Any]:
    """Exchange a public_token for a permanent access_token. Stores token in
    Keychain. Caches institution + accounts metadata in SQLite.
    """
    alias = (institution_alias or "").strip().lower()
    pt = (public_token or "").strip()
    if not alias or not pt:
        return {"ok": False, "error": "institution_alias and public_token required"}
    try:
        client = get_client()
    except PlaidConfigError as e:
        return {"ok": False, "error": str(e)}

    try:
        exchange = client.item_public_token_exchange(
            ItemPublicTokenExchangeRequest(public_token=pt)
        )
    except ApiException as e:
        db.audit_log("link_complete", alias, success=False, error=str(e.body))
        return {"ok": False, "error": _plaid_err(e)}

    access_token = exchange.access_token
    item_id = exchange.item_id

    store = keychain.put_token(alias, access_token)
    if not store.ok:
        return {"ok": False, "error": f"Keychain store failed: {store.error}"}

    # fetch institution metadata + accounts for the cache
    from plaid.model.accounts_get_request import AccountsGetRequest

    accounts_summary: list[dict[str, Any]] = []
    institution_id: str | None = None
    institution_name: str | None = None
    try:
        accts_resp = client.accounts_get(AccountsGetRequest(access_token=access_token))
        institution_id = accts_resp.item.institution_id
        for a in accts_resp.accounts:
            accounts_summary.append(
                {
                    "account_id": a.account_id,
                    "name": a.name,
                    "official_name": getattr(a, "official_name", None) or a.name,
                    "mask": a.mask,
                    "type": str(a.type),
                    "subtype": str(a.subtype) if a.subtype else None,
                }
            )
    except ApiException:
        pass

    if institution_id:
        try:
            from plaid.model.institutions_get_by_id_request import (
                InstitutionsGetByIdRequest,
            )

            inst_resp = client.institutions_get_by_id(
                InstitutionsGetByIdRequest(
                    institution_id=institution_id,
                    country_codes=[CountryCode(c) for c in DEFAULT_COUNTRIES],
                )
            )
            institution_name = inst_resp.institution.name
        except ApiException:
            pass

    db.upsert_item(
        institution_alias=alias,
        institution_id=institution_id,
        institution_name=institution_name,
        plaid_item_id=item_id,
        accounts=accounts_summary,
    )
    db.audit_log("link_complete", alias, success=True, detail=f"item_id={item_id}")

    return {
        "ok": True,
        "institution_alias": alias,
        "institution_name": institution_name,
        "accounts_linked": len(accounts_summary),
        "accounts": [
            {"name": a["name"], "mask": a["mask"], "type": a["type"], "subtype": a.get("subtype")}
            for a in accounts_summary
        ],
    }


def list_linked() -> dict[str, Any]:
    """Return all linked institutions with metadata + last-sync timestamps."""
    rows = db.list_items()
    out = []
    for r in rows:
        token_present = keychain.get_token(r["institution_alias"]) is not None
        out.append(
            {
                "institution_alias": r["institution_alias"],
                "institution_name": r["institution_name"],
                "accounts_cached": len(_safe_json(r["accounts_json"]) or []),
                "token_present": token_present,
                "last_balance_sync": r["last_balance_sync"],
                "last_holdings_sync": r["last_holdings_sync"],
                "last_tx_sync": r["last_tx_sync"],
                "last_liab_sync": r["last_liab_sync"],
                "created_at": r["created_at"],
            }
        )
    return {"ok": True, "linked": out, "count": len(out)}


def unlink(institution_alias: str) -> dict[str, Any]:
    """Remove an institution. Calls Plaid /item/remove, deletes Keychain
    entry, deletes SQLite rows. Idempotent.
    """
    alias = (institution_alias or "").strip().lower()
    if not alias:
        return {"ok": False, "error": "institution_alias required"}
    token = keychain.get_token(alias)
    if token:
        try:
            client = get_client()
            client.item_remove(ItemRemoveRequest(access_token=token))
        except (ApiException, PlaidConfigError):
            # Continue with local cleanup even if Plaid call fails
            pass
        keychain.delete_token(alias)
    deleted = db.delete_item(alias)
    db.audit_log("unlink", alias, success=True, detail=f"sqlite_rows_deleted={deleted}")
    return {"ok": True, "institution_alias": alias, "removed": deleted > 0 or token is not None}


def _plaid_err(e: ApiException) -> str:
    try:
        import json
        body = json.loads(e.body) if e.body else {}
        return body.get("error_message") or body.get("error_code") or str(e)
    except Exception:
        return str(e)


def _safe_json(s: str | None) -> Any:
    if not s:
        return None
    try:
        import json
        return json.loads(s)
    except (TypeError, ValueError):
        return None
