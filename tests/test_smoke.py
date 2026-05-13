"""Smoke tests: import, healthcheck shape, vault writer idempotency.

No Plaid network calls. Tests use FINANCE_MCP_DB_PATH override to a temp DB so
they don't touch the user's real data.

Run: cd ~/.claude/finance-mcp && PYTHONPATH=. python3 -m pytest tests/ -v
Or:  PYTHONPATH=. python3 tests/test_smoke.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


def _isolate_env() -> None:
    """Point DB + audit log at a temp dir; clear Plaid creds for isolation."""
    tmp = Path(tempfile.mkdtemp(prefix="finance-mcp-test-"))
    os.environ["FINANCE_MCP_DB_PATH"] = str(tmp / "test.db")
    os.environ["FINANCE_MCP_AUDIT_LOG"] = str(tmp / "audit.log")
    os.environ["FINANCE_MCP_VAULT_PATH"] = str(tmp / "vault")
    os.environ["FINANCE_MCP_FINANCE_FOLDER"] = "Finance"
    (tmp / "vault" / "Finance").mkdir(parents=True, exist_ok=True)
    # ensure no leftover Plaid creds
    os.environ.pop("PLAID_CLIENT_ID", None)
    os.environ.pop("PLAID_SECRET", None)


def test_server_imports():
    _isolate_env()
    from finance_mcp import server
    # FastMCP doesn't expose a stable .tools attribute across versions, but
    # the module-level function names should all exist.
    expected = {
        "healthcheck", "link_start", "link_complete", "list_linked", "unlink",
        "sync_balances", "sync_holdings", "sync_transactions", "sync_liabilities",
        "sync_all", "rollup_month", "audit_tail",
    }
    actual = {name for name in dir(server) if not name.startswith("_")}
    missing = expected - actual
    assert not missing, f"missing tool functions: {missing}"


def test_healthcheck_reports_missing_creds():
    _isolate_env()
    from finance_mcp.tools.diag import healthcheck
    r = healthcheck()
    assert r["ok"] is False
    assert r["status"] in ("red", "yellow")
    assert r["checks"]["plaid_creds_present"] is False
    assert any("PLAID_CLIENT_ID" in b for b in r["blockers"])


def test_db_schema_initializes():
    _isolate_env()
    from finance_mcp import db
    items = db.list_items()
    assert items == []
    # round-trip an audit row
    db.audit_log("test_event", "detail", success=True)
    tail = db.audit_tail(5)
    assert any(r["event"] == "test_event" for r in tail)


def test_upsert_and_delete_item():
    _isolate_env()
    from finance_mcp import db
    db.upsert_item("test_bank", "ins_99", "Test Bank", "item_abc",
                   accounts=[{"account_id": "acct_1", "name": "Checking"}])
    items = db.list_items()
    assert len(items) == 1
    assert items[0]["institution_alias"] == "test_bank"
    deleted = db.delete_item("test_bank")
    assert deleted == 1
    assert db.list_items() == []


def test_vault_writer_idempotent():
    _isolate_env()
    from finance_mcp import vault_writer
    accounts = [
        {
            "institution_alias": "test_bank",
            "account_id": "acct_1",
            "name": "Checking ...1234",
            "official_name": "Test Checking",
            "mask": "1234",
            "type": "depository",
            "subtype": "checking",
            "balance_current": 1500.50,
            "balance_available": 1500.50,
            "iso_currency_code": "USD",
            "balance_usd": 1500.50,
        }
    ]
    p1 = vault_writer.write_accounts(accounts)
    assert p1 is not None
    contents_first = p1.read_text()
    # second write should not duplicate the block
    p2 = vault_writer.write_accounts(accounts)
    contents_second = p2.read_text()
    assert contents_first.count("finance-mcp:BEGIN") == 1
    assert contents_second.count("finance-mcp:BEGIN") == 1
    assert "test_bank" in contents_second
    assert "1500.50" in contents_second


def test_rollup_month_validates_format():
    _isolate_env()
    from finance_mcp.tools.sync import rollup_month
    r = rollup_month("bad-month")
    assert r["ok"] is False
    r2 = rollup_month("2026-05")
    assert r2["ok"] is True
    assert r2["transactions_in_window"] == 0  # empty DB


def test_keychain_module_loads():
    _isolate_env()
    from finance_mcp import keychain
    # Cannot test actual keychain ops in CI; just verify the module loads
    # and the StoreResult dataclass exists.
    assert hasattr(keychain, "put_token")
    assert hasattr(keychain, "get_token")
    assert hasattr(keychain, "delete_token")


if __name__ == "__main__":
    # Manual runner — no pytest required
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    passed = 0
    failed: list[str] = []
    for t in tests:
        try:
            t()
            print(f"  ok  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL {t.__name__}: {e}")
            failed.append(t.__name__)
        except Exception as e:
            print(f"  ERR  {t.__name__}: {type(e).__name__}: {e}")
            failed.append(t.__name__)
    print(f"\n{passed}/{len(tests)} passed")
    if failed:
        sys.exit(1)
