## finance-mcp

Local-first personal finance MCP. Aggregates bank, brokerage, credit, and loan accounts via Plaid. Writes balances, holdings, and transactions to your local Obsidian-style markdown vault. Access tokens stay in macOS Keychain. Never sends data anywhere except plaid.com.

### What it does

- **Link any US bank or brokerage** via Plaid (Chase, Citi, Schwab, Discover, Fidelity, Wells Fargo, Bank of America, Capital One, Vanguard, Robinhood, Coinbase, student-loan servicers, 12,000+ institutions total).
- **Pull live balances** for every account → writes the auto-sync block in your vault's `Accounts.md`.
- **Pull investment holdings** with shares, cost basis, current value → writes `Investments.md`.
- **Pull transactions** cursor-style (delta only after first sync) → stores in local SQLite.
- **Roll up monthly cash flow** by category → writes `Cash Flow.md`.
- **Pull liabilities** (credit-card APRs, statement balances, student-loan payoff info).
- **Audit log** every Plaid call and every keychain access.

### Why local-first matters

Most personal-finance SaaS (Mint, Copilot, YNAB, Monarch) puts your bank data on their servers and charges you for the privilege. This MCP:

- **Runs on your laptop**, talks directly to Plaid, writes to your local vault.
- **Stores access tokens in macOS Keychain**, not env files or any database.
- **Never returns access tokens** in tool responses — items are referenced by alias (`chase`, `citi`, etc.).
- **Vault data is plain markdown** with inline Dataview fields — your data, your format, queryable forever.

### Install

Requires macOS (for Keychain). Python 3.11+.

```bash
git clone https://github.com/adelaidasofia/finance-mcp ~/.claude/finance-mcp
cd ~/.claude/finance-mcp
pip3 install --break-system-packages -r requirements.txt
cp .env.example .env
chmod 600 .env
```

Open `.env` and fill in your Plaid `PLAID_CLIENT_ID` and `PLAID_SECRET` (see [SETUP.md](SETUP.md) for the Plaid signup walkthrough).

### Register with Claude Code

Add to your vault's `.mcp.json` (project scope) or `~/.claude.json` (user scope, via `claude mcp add`):

```json
"finance": {
  "type": "stdio",
  "command": "python3",
  "args": ["-m", "finance_mcp.server"],
  "env": {
    "PYTHONPATH": "/Users/<you>/.claude/finance-mcp",
    "FINANCE_MCP_VAULT_PATH": "/Users/<you>/Documents/MyVault",
    "FINANCE_MCP_FINANCE_FOLDER": "Finance"
  }
}
```

Restart Claude Code. Tools appear under `mcp__finance__*`.

### First-time use

```
> healthcheck
```

If it shows blockers, follow them. Once green:

```
> link_start(institution_alias="chase")
```

Open the returned `link_token` in Plaid's [Link demo page](https://plaid.com/docs/quickstart/) (paste the token in the field labeled "Link Token", then click "Open Plaid Link"). Complete bank auth in the browser. Copy the `public_token` from the success page.

```
> link_complete(institution_alias="chase", public_token="public-sandbox-...")
```

Repeat for each bank.

```
> sync_balances
> sync_holdings
> sync_transactions
> rollup_month(month="2026-05")
```

Your vault's `Accounts.md`, `Investments.md`, and `Cash Flow.md` now have auto-sync blocks with current data. Re-run any time.

### Tool surface

| Tool | What it does |
|---|---|
| `healthcheck` | Verify Plaid creds, vault path, keychain access. |
| `link_start(alias)` | Start linking a new bank. Returns link_token. |
| `link_complete(alias, public_token)` | Exchange public_token, store in Keychain. |
| `list_linked` | List linked institutions + last-sync timestamps. |
| `unlink(alias)` | Remove an institution. Revokes Plaid item + deletes Keychain entry. |
| `sync_balances([alias])` | Pull current balances → Accounts.md. |
| `sync_holdings([alias])` | Pull investment positions → Investments.md. |
| `sync_transactions([alias])` | Cursor-based transaction sync → SQLite. |
| `sync_liabilities([alias])` | Pull credit + loan details. |
| `sync_all([alias])` | All of the above in sequence. |
| `rollup_month(month)` | Compute monthly income/expense rollup → Cash Flow.md. |
| `audit_tail(n)` | Last N audit-log entries. |

### Security model

- Plaid `access_token` lives in macOS Keychain (`security` CLI), service name `finance-mcp`, account name = your institution alias.
- Plaid `client_id` + `secret` live in `.env` (chmod 600). Never committed (in `.gitignore`).
- Transaction history lives in SQLite at `~/.claude/finance-mcp/data.db` (not in your vault).
- Vault writes only happen inside the configured `FINANCE_MCP_FINANCE_FOLDER`. Set to empty string to disable vault writes entirely.
- Audit log records every Plaid call and every keychain operation.
- Tool responses never include raw access tokens.

### Plaid environments

- **sandbox** (default): fake banks, fake credentials (`user_good` / `pass_good`). Free forever. Use this first.
- **development**: real banks, free up to 100 items per Plaid account. Use this for personal accounts.
- **production**: real banks at scale. Requires a Plaid application + paid plan.

Set via `PLAID_ENV` in `.env`. Switch by re-linking all institutions (tokens are environment-bound).

### Companion MCPs

- [`jkoelker/schwab-mcp`](https://github.com/jkoelker/schwab-mcp) — Direct Charles Schwab Trader API for options, trade placement, and deep position data beyond what Plaid exposes.
- [`tomasgesino/schwab-mcp`](https://github.com/tomasgesino/schwab-mcp) — Schwab wheel-strategy management with dry-run-default trading.

`finance-mcp` covers Schwab basic balances and holdings via Plaid; the above are the route for Schwab power-user features.

### Related MCPs in this family

- [apollo-mcp](https://github.com/adelaidasofia/apollo-mcp) — Apollo.io CRM + outbound sequences.
- [slack-mcp](https://github.com/adelaidasofia/slack-mcp) — Multi-workspace Slack with draft+confirm safety.
- [imessage-mcp](https://github.com/adelaidasofia/imessage-mcp) — Local iMessage with Whisper voice transcription.
- [whatsapp-mcp](https://github.com/adelaidasofia/whatsapp-mcp) — WhatsApp via local bridge.
- [substack-mcp](https://github.com/adelaidasofia/substack-mcp) — Publish posts + Notes, pull analytics.
- [parse-mcp](https://github.com/adelaidasofia/parse-mcp) — Multi-backend document parsing router.
- [graph-query-mcp](https://github.com/adelaidasofia/graph-query-mcp) — Personal knowledge graph queries.

### License

MIT. See [LICENSE](LICENSE).

---

Built by Adelaida Diaz-Roa. Full install or team version at diazroa.com.
