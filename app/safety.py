import httpx
import time

RUGCHECK_BASE = "https://api.rugcheck.xyz/v1"
_safety_cache: dict[str, dict] = {}   # mint -> result, TTL-based
_CACHE_TTL_SECONDS = 300

def classify_risk(score) -> str:
    if score is None:
        return "UNKNOWN"
    if score < 30:
        return "LOW"
    if score < 60:
        return "MODERATE"
    return "HIGH"

def extract_top_holder_pct(top_holders: list) -> float | None:
    if not top_holders:
        return None
    # Exclude LP/burn addresses if flagged; take largest remaining holder
    non_lp = [h for h in top_holders if not h.get("isLP", False)]
    pool = non_lp or top_holders
    return max((h.get("pct", 0) for h in pool), default=None)

async def get_safety_report(mint: str) -> dict:
    """
    Fetch and normalize a safety report for a token mint from RugCheck.
    Returns a dict with fields:
      - risk_score: int | None       (0-100+, lower is safer)
      - risk_level: str              ("LOW" | "MODERATE" | "HIGH" | "UNKNOWN")
      - mint_revoked: bool | None
      - freeze_revoked: bool | None
      - liquidity_usd: float | None
      - top_holder_pct: float | None  (% held by largest holder, excluding LP)
      - red_flags: list[str]          (human-readable risk names, max 3)
      - is_stale: bool                (True if fetch failed, using cached/default)
    """
    now = time.time()
    cached = _safety_cache.get(mint)
    if cached and (now - cached["_fetched_at"]) < _CACHE_TTL_SECONDS:
        return cached

    # Use the detailed report endpoint since summary endpoint lacks token/holders/liquidity fields.
    url = f"{RUGCHECK_BASE}/tokens/{mint}/report"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        print(f"[safety] RugCheck call failed for {mint}: {exc}")
        # Fail open with UNKNOWN — never block an alert because a third-party API is down
        return {
            "risk_score": None,
            "risk_level": "UNKNOWN",
            "mint_revoked": None,
            "freeze_revoked": None,
            "liquidity_usd": None,
            "top_holder_pct": None,
            "red_flags": [],
            "is_stale": True,
        }

    score = data.get("score_normalised") or data.get("score")
    risk_level = classify_risk(score)
    risks = data.get("risks", [])
    red_flags = [r.get("name", "") for r in risks if r.get("level") in ("danger", "warn")][:3]

    result = {
        "risk_score": score,
        "risk_level": risk_level,
        "mint_revoked": data.get("token", {}).get("mintAuthority") is None,
        "freeze_revoked": data.get("token", {}).get("freezeAuthority") is None,
        "liquidity_usd": data.get("totalMarketLiquidity"),
        "top_holder_pct": extract_top_holder_pct(data.get("topHolders", [])),
        "red_flags": red_flags,
        "is_stale": False,
        "_fetched_at": now,
    }
    _safety_cache[mint] = result
    return result
