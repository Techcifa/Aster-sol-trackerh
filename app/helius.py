"""
app/helius.py

Helius API client responsible for:
  1. Webhook lifecycle  — create on first boot, add/remove wallet addresses.
  2. getBalance RPC     — fetch live SOL balance for a wallet (lamports → SOL).
  3. DAS getAsset       — fetch token symbol/name/decimals with in-memory cache.

All HTTP calls use a shared httpx.AsyncClient context.
API keys are always read from settings — never hardcoded.
"""

import os
import httpx
from app.config import settings
from app import database as db

# ---------------------------------------------------------------------------
# In-memory token metadata cache
# key: token_mint str
# value: {"symbol": str, "name": str, "decimals": int}
# ---------------------------------------------------------------------------
_metadata_cache: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# Helius REST API base URL (webhooks management)
# ---------------------------------------------------------------------------
_HELIUS_API_BASE = "https://api.helius.xyz/v0"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _webhook_url() -> str:
    return f"{_HELIUS_API_BASE}/webhooks?api-key={settings.HELIUS_API_KEY}"


def _webhook_id_url() -> str:
    return (
        f"{_HELIUS_API_BASE}/webhooks/{settings.HELIUS_WEBHOOK_ID}"
        f"?api-key={settings.HELIUS_API_KEY}"
    )


def _webhook_body(addresses: list[str]) -> dict:
    """Build the full webhook PUT/POST payload."""
    return {
        "webhookURL": settings.helius_webhook_url,
        "transactionTypes": ["ANY"],
        "accountAddresses": addresses,
        "webhookType": "enhanced",
        "authHeader": "",
    }


def _save_webhook_id_to_env(webhook_id: str) -> None:
    """
    Write HELIUS_WEBHOOK_ID back into the .env file so it persists across
    restarts.  Also updates the in-process settings object immediately.
    """
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    env_path = os.path.normpath(env_path)

    # Read existing lines
    lines: list[str] = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    # Replace or append
    key = "HELIUS_WEBHOOK_ID"
    new_line = f"{key}={webhook_id}\n"
    replaced = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = new_line
            replaced = True
            break
    if not replaced:
        lines.append(new_line)

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    # Update the live settings object so subsequent calls use the new ID
    settings.HELIUS_WEBHOOK_ID = webhook_id


# ---------------------------------------------------------------------------
# Webhook lifecycle
# ---------------------------------------------------------------------------

async def ensure_webhook_registered() -> None:
    """
    Called once at startup (from main.py).

    - If HELIUS_WEBHOOK_ID is already set → nothing to do.
    - If not set → POST to create a new webhook and persist the returned ID.
    """
    if settings.HELIUS_WEBHOOK_ID:
        print(
            f"[helius] Webhook already registered: {settings.HELIUS_WEBHOOK_ID}"
        )
        return

    print("[helius] No HELIUS_WEBHOOK_ID found — registering new webhook...")

    # Start with an empty address list; wallets are added dynamically via /add
    payload = _webhook_body([])

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(_webhook_url(), json=payload)
        resp.raise_for_status()
        data = resp.json()

    webhook_id: str = data["webhookID"]
    _save_webhook_id_to_env(webhook_id)
    print(f"[helius] Webhook registered successfully: {webhook_id}")


async def add_wallet_to_webhook(wallet: str) -> None:
    """
    Add a wallet to the Helius webhook by fetching ALL currently tracked
    unique wallets from the DB and doing a full-replace PUT.
    """
    all_wallets = await db.get_all_unique_wallets()

    # Ensure the new wallet is present (it was just inserted into the DB)
    if wallet not in all_wallets:
        all_wallets.append(wallet)

    await _put_webhook_addresses(all_wallets)
    print(f"[helius] Webhook updated — added wallet. Total tracked: {len(all_wallets)}")


async def remove_wallet_from_webhook(wallet: str) -> None:
    """
    Remove a wallet from the Helius webhook.
    Fetches the current DB list (wallet was already deleted) and does a
    full-replace PUT without the removed address.
    """
    all_wallets = await db.get_all_unique_wallets()
    # Safety: remove in case it somehow still appears
    all_wallets = [w for w in all_wallets if w != wallet]

    await _put_webhook_addresses(all_wallets)
    print(f"[helius] Webhook updated — removed wallet. Total tracked: {len(all_wallets)}")


async def _put_webhook_addresses(addresses: list[str]) -> None:
    """
    Full-replace PUT of the Helius webhook address list.
    This is the only correct way to update Helius webhook addresses —
    incremental patches are not supported.
    """
    if not settings.HELIUS_WEBHOOK_ID:
        raise RuntimeError(
            "HELIUS_WEBHOOK_ID is not set — cannot update webhook. "
            "Call ensure_webhook_registered() first."
        )

    payload = _webhook_body(addresses)

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.put(_webhook_id_url(), json=payload)
        resp.raise_for_status()


# ---------------------------------------------------------------------------
# Solana RPC — getBalance
# ---------------------------------------------------------------------------

async def get_sol_balance(wallet: str) -> float:
    """
    Fetch the current SOL balance for a wallet via the Helius RPC endpoint.
    Returns the balance in SOL (lamports ÷ 1_000_000_000).
    """
    payload = {
        "jsonrpc": "2.0",
        "id": "get-balance",
        "method": "getBalance",
        "params": [wallet],
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(settings.helius_rpc_url, json=payload)
        resp.raise_for_status()
        data = resp.json()

    lamports: int = data["result"]["value"]
    return lamports / 1_000_000_000


# ---------------------------------------------------------------------------
# DAS API — getAsset (token metadata)
# ---------------------------------------------------------------------------

async def get_token_metadata(token_mint: str) -> dict:
    """
    Fetch token symbol, name, and decimals via the Helius DAS API.

    Returns:
        {
            "symbol":   str,   # e.g. "BONK"
            "name":     str,   # e.g. "Bonk"
            "decimals": int    # e.g. 5
        }

    Falls back to {"symbol": token_mint[:6], "name": "Unknown", "decimals": 6}
    on any error so the rest of the pipeline never breaks on a bad mint.

    Results are cached in _metadata_cache to avoid redundant network calls.
    """
    if token_mint in _metadata_cache:
        return _metadata_cache[token_mint]

    payload = {
        "jsonrpc": "2.0",
        "id": "get-asset",
        "method": "getAsset",
        "params": {"id": token_mint},
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(settings.helius_rpc_url, json=payload)
            resp.raise_for_status()
            data = resp.json()

        result = data.get("result", {})
        content = result.get("content", {})
        metadata = content.get("metadata", {})
        token_info = result.get("token_info", {})

        symbol: str = metadata.get("symbol") or token_mint[:6]
        name: str = metadata.get("name") or "Unknown"
        decimals: int = token_info.get("decimals", 6)

        meta = {"symbol": symbol, "name": name, "decimals": decimals}

    except Exception as exc:  # noqa: BLE001
        print(f"[helius] getAsset failed for {token_mint}: {exc}")
        meta = {"symbol": token_mint[:6], "name": "Unknown", "decimals": 6}

    _metadata_cache[token_mint] = meta
    return meta


_supply_cache: dict[str, float] = {}


async def get_token_supply(token_mint: str) -> float:
    """
    Fetch the total supply of a token mint using the Solana JSON-RPC getTokenSupply endpoint.
    Caches the results to prevent repeated RPC queries.
    """
    if token_mint in _supply_cache:
        return _supply_cache[token_mint]

    payload = {
        "jsonrpc": "2.0",
        "id": "get-token-supply",
        "method": "getTokenSupply",
        "params": [token_mint],
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(settings.helius_rpc_url, json=payload)
            resp.raise_for_status()
            data = resp.json()

        supply_val = data["result"]["value"]["uiAmount"]
        if supply_val is not None:
            _supply_cache[token_mint] = float(supply_val)
            return float(supply_val)
    except Exception as exc:
        print(f"[helius] Failed to fetch supply for {token_mint}: {exc}")

    return 1_000_000_000.0  # Fallback default supply (1 Billion)

