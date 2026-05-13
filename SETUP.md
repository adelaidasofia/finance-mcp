## finance-mcp Setup

End-to-end walkthrough: Plaid signup → install → link your first bank → first sync.

### Step 1: Plaid developer signup (5 min)

1. Go to [dashboard.plaid.com/signup](https://dashboard.plaid.com/signup).
2. Fill in name + email + create password. Free tier.
3. Verify your email.
4. On the dashboard, you'll land in the **Sandbox** environment by default.
5. Click **Team Settings → Keys** in the left nav.
6. Copy your **client_id** and the **sandbox secret** (we'll get production keys later if you graduate).

### Step 2: Install finance-mcp (3 min)

```bash
git clone https://github.com/adelaidasofia/finance-mcp ~/.claude/finance-mcp
cd ~/.claude/finance-mcp
pip3 install --break-system-packages -r requirements.txt
```

If your system Python is managed by `uv` (Mac with Homebrew + modern-python-substrate), use the homebrew pip explicitly:

```bash
/opt/homebrew/bin/pip3 install --break-system-packages -r requirements.txt
```

### Step 3: Configure (.env)

```bash
cd ~/.claude/finance-mcp
cp .env.example .env
chmod 600 .env
```

Open `.env` in your editor and fill in:

```
PLAID_CLIENT_ID=<paste from Plaid dashboard>
PLAID_SECRET=<paste sandbox secret>
PLAID_ENV=sandbox
FINANCE_MCP_VAULT_PATH=/Users/<you>/Documents/MyVault
FINANCE_MCP_FINANCE_FOLDER=Finance
```

Set `FINANCE_MCP_FINANCE_FOLDER` to the relative path inside your vault where finance markdown files should live. The folder must exist (the MCP doesn't auto-create vault folders). If your vault doesn't have one yet:

```bash
mkdir -p "/Users/<you>/Documents/MyVault/Finance"
```

### Step 4: Verify

```bash
cd ~/.claude/finance-mcp
PYTHONPATH=. python3 -c "from finance_mcp.tools.diag import healthcheck; import json; print(json.dumps(healthcheck(), indent=2, default=str))"
```

Expect `"status": "green"` with no blockers. If yellow or red, the output tells you which env var or folder is missing.

### Step 5: Register with Claude Code

Add to your project `.mcp.json` (in your vault root) OR run:

```bash
claude mcp add -s user finance python3 -m finance_mcp.server \
  -e PYTHONPATH=$HOME/.claude/finance-mcp \
  -e FINANCE_MCP_VAULT_PATH=$HOME/Documents/MyVault \
  -e FINANCE_MCP_FINANCE_FOLDER=Finance
```

Restart Claude Code. Tools appear as `mcp__finance__healthcheck`, `mcp__finance__link_start`, etc.

### Step 6: Sandbox link (first end-to-end test)

In Claude Code:

```
> healthcheck
```

Should return green.

```
> link_start(institution_alias="sandbox_bank")
```

You'll get back a `link_token` and instructions. Easiest path:

1. Open [plaid.com/docs/quickstart/](https://plaid.com/docs/quickstart/) in your browser.
2. Click **Use Quickstart in browser**.
3. Paste the `link_token` into the form. Click **Open Plaid Link**.
4. In the Plaid Link dialog, pick **First Platypus Bank** (or any sandbox bank).
5. Enter credentials: `user_good` / `pass_good`. (For MFA banks, `1234` is the code.)
6. Click **Continue**. Plaid Link returns a `public_token` — copy it from the success page.

Back in Claude Code:

```
> link_complete(institution_alias="sandbox_bank", public_token="public-sandbox-...")
```

Returns the linked accounts list.

```
> sync_balances
```

Writes the auto-sync block to your vault's `Finance/Accounts.md`.

```
> sync_transactions
> rollup_month(month="2026-05")
```

Writes `Cash Flow.md` with monthly rollup.

### Step 7: Graduate to development (real banks)

When ready for real banks:

1. Plaid dashboard → **Apply for Development access**. Free, instant approval for most.
2. Copy the **development secret** (separate from sandbox).
3. Edit `.env`:
   ```
   PLAID_SECRET=<development secret>
   PLAID_ENV=development
   ```
4. Restart Claude Code.
5. `unlink(institution_alias="sandbox_bank")` to clear sandbox state.
6. `link_start(institution_alias="chase")` and walk through the same flow — Plaid Link now shows real banks. Sign in with your actual bank credentials. Plaid handles MFA, never shares the password with this MCP.

Development tier covers 100 items (institutions) free. Plenty for personal use; you'll never need production.

### Schwab note

Schwab works via Plaid for balances and holdings. For options chains, lot-level cost basis, and trade placement, install [jkoelker/schwab-mcp](https://github.com/jkoelker/schwab-mcp) alongside — it's a separate MCP that talks directly to Schwab's Trader API.

### Storage paths

- Plaid `access_token` per institution → macOS Keychain (`security` command, service `finance-mcp`)
- Plaid `client_id` + `secret` → `~/.claude/finance-mcp/.env` (chmod 600)
- Transaction history + audit log → `~/.claude/finance-mcp/data.db`
- Audit text log → `~/.claude/finance-mcp/audit.log`
- Markdown writes → `<your vault>/<finance folder>/Accounts.md` + `Investments.md` + `Cash Flow.md`

### Backup advice

- Keychain entries: `security export -k <your login keychain> -o ~/Desktop/keychain-backup.p12` (protect with strong password)
- SQLite: `cp ~/.claude/finance-mcp/data.db ~/Desktop/finance-mcp-data-$(date +%Y%m%d).db`
- Markdown: your vault's existing git or Time Machine backup covers this.

### Troubleshooting

**healthcheck shows red, "PLAID_CLIENT_ID missing"** — `.env` not in `~/.claude/finance-mcp/`, or env vars not loaded. Check `chmod 600 .env` and that the file lives at the MCP root.

**healthcheck yellow, "Finance folder missing"** — create the folder at `<vault>/Finance` or change `FINANCE_MCP_FINANCE_FOLDER` to point at an existing folder. Set to empty string to disable vault writes.

**link_complete returns "INVALID_PUBLIC_TOKEN"** — public_tokens expire 30 min after Plaid Link returns them. Re-run `link_start` and complete the Plaid Link flow within 30 minutes.

**sync_holdings returns empty + no errors** — most checking-only banks don't support the Plaid investments product. This is expected. Investments come from Schwab / Fidelity / Vanguard / Robinhood / etc.

**Plaid Link won't load in browser** — the Plaid Quickstart page requires a frontend SDK. If you're behind a strict CSP or offline, use [github.com/plaid/quickstart](https://github.com/plaid/quickstart) locally instead.

### Privacy

This MCP:
- Talks to Plaid (`plaid.com`) ONLY.
- Stores all data on YOUR machine.
- Never logs your access_token.
- Never returns your access_token in any tool response.
- Audits every call so you can verify the above.

If you'd like to add a network-traffic firewall layer, point Little Snitch or LuLu at the `python3` process and approve only `*.plaid.com` outbound.
