"""finance-mcp: local-first personal finance MCP, Plaid backbone.

Tokens in macOS Keychain. Data in your local markdown vault + SQLite.
Never sends data anywhere except plaid.com.
"""

from __future__ import annotations

import os
from pathlib import Path

__version__ = "0.1.0"


def _load_dotenv() -> None:
    """Minimal .env loader. Runs at package import so all entry points
    (server, healthcheck, tests) see the same env. Does NOT override values
    already set in the environment (e.g. from .mcp.json `env` block).
    """
    here = Path(__file__).resolve().parent.parent
    env_file = here / ".env"
    if not env_file.exists():
        return
    try:
        for raw in env_file.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass


_load_dotenv()
