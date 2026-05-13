"""Plaid SDK wrapper. Loads credentials from env, returns a configured client.

Environments:
- sandbox      → https://sandbox.plaid.com         (fake banks, test_user_good / pass_good)
- development  → https://development.plaid.com    (real banks, 100 free items)
- production   → https://production.plaid.com    (real banks, paid plan, requires app)

All tools call the same client; the env determines which Plaid backend is hit.

Token security: access_tokens passed in via Keychain at call time, never logged,
never returned in tool responses (tool layer aliases items by institution_alias).
"""

from __future__ import annotations

import os
from typing import Any, Optional

import plaid
from plaid.api import plaid_api


_HOSTS = {
    "sandbox": plaid.Environment.Sandbox,
    "development": getattr(plaid.Environment, "Development", plaid.Environment.Sandbox),
    "production": plaid.Environment.Production,
}


class PlaidConfigError(RuntimeError):
    pass


def _resolve_env() -> str:
    raw = os.environ.get("PLAID_ENV", "").strip().lower()
    if raw in _HOSTS:
        return raw
    return "sandbox"


def _resolve_creds() -> tuple[str, str]:
    cid = os.environ.get("PLAID_CLIENT_ID", "").strip()
    secret = os.environ.get("PLAID_SECRET", "").strip()
    if not cid or not secret:
        raise PlaidConfigError(
            "PLAID_CLIENT_ID and PLAID_SECRET must be set. Copy .env.example to .env "
            "and fill in your developer credentials from https://dashboard.plaid.com."
        )
    return cid, secret


def get_client() -> plaid_api.PlaidApi:
    env = _resolve_env()
    cid, secret = _resolve_creds()
    configuration = plaid.Configuration(
        host=_HOSTS[env],
        api_key={
            "clientId": cid,
            "secret": secret,
        },
    )
    api_client = plaid.ApiClient(configuration)
    return plaid_api.PlaidApi(api_client)


def env_summary() -> dict[str, Any]:
    """Diagnostic, non-secret: which env, whether creds present."""
    try:
        cid, secret = _resolve_creds()
        creds_present = True
        masked = f"{cid[:4]}...{cid[-4:]}" if len(cid) > 8 else "set"
    except PlaidConfigError:
        creds_present = False
        masked = None
    return {
        "env": _resolve_env(),
        "creds_present": creds_present,
        "client_id_masked": masked,
        "webhook_url": os.environ.get("PLAID_WEBHOOK_URL", "") or None,
    }


# Plaid products requested at link time.
# `transactions` covers banks + credit cards.
# `investments` covers brokerage / retirement holdings + trades.
# `liabilities` covers credit-card APRs, student loans, mortgages.
# `identity` is a sanity check; tells us account-holder name.
DEFAULT_PRODUCTS = ["transactions", "investments", "liabilities"]
DEFAULT_OPTIONAL = ["identity"]

# Countries — US is the primary scope; CA + GB available if needed
DEFAULT_COUNTRIES = ["US"]
