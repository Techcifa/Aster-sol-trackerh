"""
app/jupiter.py

Jupiter Price API v3 client.

Endpoint:
    GET https://api.jup.ag/price/v3
        ?ids={token_mints}

Returns the token price in USD. The client converts it to SOL dynamically
by fetching the live USD price of SOL and calculating token_usd / sol_usd.

Cache:
    Prices are cached in-memory with a 30-second TTL to avoid hammering
    the public API on every swap alert.
    Returns 0.0 and logs a warning on any error so callers never break.
"""

import time
import httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_JUPITER_PRICE_URL = "https://api.jup.ag/price/v3"

# Wrapped SOL mint
_WSOL_MINT = "So11111111111111111111111111111111111111112"

# Cache TTL in seconds
_CACHE_TTL_SECONDS = 30

# ---------------------------------------------------------------------------
# In-memory price cache
# key: token_mint str
# value: {"price": float, "fetched_at": float}
# ---------------------------------------------------------------------------
_price_cache: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_price(token_mint: str) -> float:
    """
    Return the current price of `token_mint` in SOL.

    - Checks the in-memory cache first; returns cached value if < 30 s old.
    - On cache miss, fetches from Jupiter Price API v3.
    - Returns 0.0 on any network or parsing error (never raises).
    """
    # ---- cache hit ----
    cached = _price_cache.get(token_mint)
    if cached is not None:
        age = time.monotonic() - cached["fetched_at"]
        if age < _CACHE_TTL_SECONDS:
            return cached["price"]

    # ---- fetch ----
    price = await _fetch_price(token_mint)

    # ---- populate cache ----
    _price_cache[token_mint] = {"price": price, "fetched_at": time.monotonic()}
    return price


async def get_prices(token_mints: list[str]) -> dict[str, float]:
    """
    Batch-fetch prices for multiple mints in a single HTTP request.
    Returns a dict of {token_mint: price_in_sol}.
    Missing or errored mints get price 0.0.
    """
    now = time.monotonic()
    to_fetch: list[str] = []
    result: dict[str, float] = {}

    for mint in token_mints:
        cached = _price_cache.get(mint)
        if cached is not None and (now - cached["fetched_at"]) < _CACHE_TTL_SECONDS:
            result[mint] = cached["price"]
        else:
            to_fetch.append(mint)

    if to_fetch:
        fetched = await _fetch_prices_batch(to_fetch)
        for mint in to_fetch:
            price = fetched.get(mint, 0.0)
            _price_cache[mint] = {"price": price, "fetched_at": time.monotonic()}
            result[mint] = price

    return result


# ---------------------------------------------------------------------------
# Cache inspection helpers (useful for testing)
# ---------------------------------------------------------------------------

def clear_cache() -> None:
    """Flush the entire price cache. Useful in tests."""
    _price_cache.clear()


def invalidate(token_mint: str) -> None:
    """Force-expire a single mint's cache entry."""
    _price_cache.pop(token_mint, None)


# ---------------------------------------------------------------------------
# Internal fetch helpers
# ---------------------------------------------------------------------------

async def _fetch_price(token_mint: str) -> float:
    """Single-mint fetch. Returns 0.0 on any error."""
    prices = await _fetch_prices_batch([token_mint])
    return prices.get(token_mint, 0.0)


async def _fetch_prices_batch(token_mints: list[str]) -> dict[str, float]:
    """
    Fetch one or more mint prices from Jupiter in one request.
    Denominates them in SOL by dividing their USD price by the USD price of SOL.
    """
    sol_price_usd = await get_sol_price_in_usd()
    if sol_price_usd <= 0.0:
        sol_price_usd = 140.0  # Safe fallback

    ids_param = ",".join(token_mints)
    params = {
        "ids": ids_param,
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(_JUPITER_PRICE_URL, params=params)
            resp.raise_for_status()
            payload = resp.json()

        result: dict[str, float] = {}
        for mint in token_mints:
            entry = payload.get(mint)
            if entry and "usdPrice" in entry:
                usd_price = float(entry["usdPrice"])
                result[mint] = usd_price / sol_price_usd
            else:
                result[mint] = 0.0
        return result

    except Exception as exc:  # noqa: BLE001
        print(f"[jupiter] Price fetch failed for {token_mints}: {exc}")
        return {mint: 0.0 for mint in token_mints}


async def get_sol_price_in_usd() -> float:
    """
    Return the current price of SOL in USD.
    Uses the in-memory cache if it is fresh (< 30 seconds old).
    """
    cached = _price_cache.get("SOL_USD")
    if cached is not None:
        age = time.monotonic() - cached["fetched_at"]
        if age < _CACHE_TTL_SECONDS:
            return cached["price"]

    params = {
        "ids": _WSOL_MINT
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(_JUPITER_PRICE_URL, params=params)
            resp.raise_for_status()
            payload = resp.json()

        entry = payload.get(_WSOL_MINT)
        price = float(entry["usdPrice"]) if entry and "usdPrice" in entry else 140.0
    except Exception as exc:
        print(f"[jupiter] SOL USD price fetch failed: {exc}")
        price = 140.0  # Safe fallback

    _price_cache["SOL_USD"] = {"price": price, "fetched_at": time.monotonic()}
    return price
