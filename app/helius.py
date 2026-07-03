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
# value: {"symbol": str, "name": str, "decimals": int, "supply": float, "is_fallback": bool}
# Entries with is_fallback=True are NOT stored here — they are returned directly
# so the next call retries the network instead of poisoning the cache forever.
# ---------------------------------------------------------------------------
_metadata_cache: dict[str, dict] = {}

# Cache for token launch times (Unix epoch timestamps).
# Since a token's launch time never changes once determined, we cache this permanently.
_launch_time_cache: dict[str, int] = {}


def _normalize_supply(raw_supply: float, decimals: int) -> float:
    """
    Safely convert a Helius token_info.supply value to a human-readable float.

    Helius DAS getAsset always returns token_info.supply as the raw on-chain
    integer (smallest unit), so we divide by 10**decimals.  However, if the
    value were somehow already adjusted (e.g. routed through getTokenSupply's
    uiAmount field by mistake) dividing again would make it absurdly small.

    Heuristic: after dividing, if the result would be < 1_000 tokens it is
    almost certainly wrong for a standard SPL token, which means the value
    was already decimal-adjusted upstream — so we skip the division.
    """
    supply = float(raw_supply)
    if decimals > 0:
        divided = supply / (10 ** decimals)
        # A legitimate post-division supply should be at least 1 000 tokens.
        # If it isn't, the caller already passed an adjusted value.
        if divided >= 1_000:
            return divided
        # Looks pre-adjusted — return as-is
        print(
            f"[helius] normalize_supply: skipping division (divided={divided:.4f} < 1000) "
            f"raw={raw_supply} decimals={decimals} — treating as already adjusted"
        )
        return supply
    return supply

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
    Fetch token symbol, name, decimals, and total supply via the Helius DAS API.

    Returns on success:
        {
            "symbol":      str,   # e.g. "BONK"
            "name":        str,   # e.g. "Bonk"
            "decimals":    int,   # e.g. 5
            "supply":      float, # decimal-adjusted total supply
            "is_fallback": False
        }

    Returns on failure (NOT cached — retried on next call):
        {
            "symbol":      str,
            "name":        "Unknown",
            "decimals":    6,
            "supply":      None,   # Callers must treat None as "unavailable"
            "is_fallback": True
        }
    """
    # Only return cached entry when it is real data, never a stale fallback.
    cached = _metadata_cache.get(token_mint)
    if cached is not None and not cached.get("is_fallback", False):
        return cached

    payload = {
        "jsonrpc": "2.0",
        "id": "get-asset",
        "method": "getAsset",
        "params": {
            "id": token_mint,
            "options": {
                "showFungible": True
            }
        },
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

        # Log raw token_info once per mint so we can verify the format manually.
        print(f"[helius] token_info for {token_mint}: {token_info}")

        symbol: str = metadata.get("symbol") or token_mint[:6]
        name: str = metadata.get("name") or "Unknown"
        decimals: int = token_info.get("decimals", 6)

        raw_supply = token_info.get("supply", 0)
        supply = _normalize_supply(float(raw_supply), decimals) if raw_supply else 0.0

        if supply <= 0:
            # Real supply is unavailable — return fallback WITHOUT caching.
            print(f"[helius] supply unavailable for {token_mint} — will retry next call")
            return {
                "symbol": symbol,
                "name": name,
                "decimals": decimals,
                "supply": None,
                "is_fallback": True,
            }

        meta = {
            "symbol": symbol,
            "name": name,
            "decimals": decimals,
            "supply": supply,
            "is_fallback": False,
        }

    except Exception as exc:  # noqa: BLE001
        print(f"[helius] getAsset failed for {token_mint}: {exc}")
        # Do NOT cache — allow a retry on the next event for this mint.
        return {
            "symbol": token_mint[:6],
            "name": "Unknown",
            "decimals": 6,
            "supply": None,
            "is_fallback": True,
        }

    # Only cache confirmed, real data.
    _metadata_cache[token_mint] = meta
    return meta


async def get_token_launch_time(mint: str) -> int | None:
    """
    Returns the estimated Unix timestamp of token creation (earliest tx on the mint account).
    Returns None if it cannot be determined within the pagination cap.
    Cache aggressively — this value never changes once found.
    """
    if mint in _launch_time_cache:
        return _launch_time_cache[mint]

    earliest_sig = None
    before = None

    async with httpx.AsyncClient(timeout=5.0) as client:
        for _ in range(3):  # pagination cap (3 pages of 1000 sigs)
            params = [mint, {"limit": 1000}]
            if before:
                params[1]["before"] = before

            payload = {
                "jsonrpc": "2.0",
                "id": "get-sigs",
                "method": "getSignaturesForAddress",
                "params": params,
            }

            try:
                resp = await client.post(settings.helius_rpc_url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                sigs = data.get("result", [])
            except Exception as exc:
                print(f"[helius] getSignaturesForAddress RPC failed for {mint}: {exc}")
                break

            if not sigs:
                break

            earliest_sig = sigs[-1]
            before = earliest_sig.get("signature")
            if len(sigs) < 1000:
                break  # reached the actual start of transactions

    launch_time = earliest_sig.get("blockTime") if earliest_sig else None
    if launch_time:
        _launch_time_cache[mint] = launch_time
    return launch_time



