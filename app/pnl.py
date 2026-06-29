"""
app/pnl.py

PnL (Profit-and-Loss) computation engine.

Uses the Average Cost method as specified in the build prompt:

    avg_cost_per_token = total_spent_sol / total_bought

Unrealized PnL:
    tokens_remaining   = total_bought - total_sold
    current_value_sol  = tokens_remaining * current_price_sol
    cost_basis_sol     = tokens_remaining * avg_cost
    unrealized_pnl_sol = current_value_sol - cost_basis_sol
    pnl_pct            = (unrealized_pnl_sol / cost_basis_sol) * 100

All functions are async and independently unit-testable (no Telegram imports).
"""

from __future__ import annotations

from app import database as db
from app import jupiter


# ---------------------------------------------------------------------------
# Unrealized PnL (exact signature from build prompt)
# ---------------------------------------------------------------------------

async def compute_unrealized_pnl(wallet: str, token_mint: str) -> dict:
    """
    Compute unrealized PnL for a single open position.

    Args:
        wallet:     Base58 wallet address.
        token_mint: Token CA (contract address / mint address).

    Returns:
        {
            "token_mint":          str,
            "token_symbol":        str,
            "tokens_held":         float,   # tokens_remaining
            "avg_cost_sol":        float,   # average cost per token in SOL
            "current_price_sol":   float,   # live price from Jupiter
            "current_value_sol":   float,   # tokens_held * current_price
            "cost_basis_sol":      float,   # tokens_held * avg_cost
            "unrealized_pnl_sol":  float,
            "pnl_pct":             float,
        }

    Raises:
        ValueError: If no position exists for (wallet, token_mint).
    """
    position = await db.get_position(wallet, token_mint)
    if position is None:
        raise ValueError(
            f"No position found for wallet={wallet} token={token_mint}"
        )

    current_price = await jupiter.get_price(token_mint)

    tokens_remaining: float = max(
        0.0,
        position["total_bought"] - position["total_sold"]
    )

    avg_cost: float = (
        position["total_spent_sol"] / position["total_bought"]
        if position["total_bought"] > 0
        else 0.0
    )

    current_value_sol: float = tokens_remaining * current_price
    cost_basis_sol: float = tokens_remaining * avg_cost
    unrealized_pnl_sol: float = current_value_sol - cost_basis_sol
    pnl_pct: float = (
        (unrealized_pnl_sol / cost_basis_sol * 100)
        if cost_basis_sol > 0
        else 0.0
    )

    return {
        "token_mint": token_mint,
        "token_symbol": position["token_symbol"] or token_mint[:6],
        "tokens_held": tokens_remaining,
        "avg_cost_sol": avg_cost,
        "current_price_sol": current_price,
        "current_value_sol": current_value_sol,
        "cost_basis_sol": cost_basis_sol,
        "unrealized_pnl_sol": unrealized_pnl_sol,
        "pnl_pct": pnl_pct,
    }


# ---------------------------------------------------------------------------
# Portfolio-level PnL (used by /pnl command)
# ---------------------------------------------------------------------------

async def compute_portfolio_pnl(wallet: str) -> dict:
    """
    Compute unrealized PnL for all open positions in a wallet.

    Only includes positions where tokens_remaining > 0.
    Results are sorted by |unrealized_pnl_sol| descending (spec requirement).

    Returns:
        {
            "wallet": str,
            "positions": [  ...sorted list of compute_unrealized_pnl() dicts... ],
            "total_unrealized_pnl_sol": float,
        }
    """
    all_positions = await db.get_all_positions_for_wallet(wallet)

    open_positions = [
        p for p in all_positions
        if (p["total_bought"] - p["total_sold"]) > 0
    ]

    pnl_results: list[dict] = []
    for pos in open_positions:
        try:
            pnl = await compute_unrealized_pnl(wallet, pos["token_mint"])
            pnl_results.append(pnl)
        except Exception as exc:  # noqa: BLE001
            # Don't let one bad mint tank the whole response
            print(f"[pnl] Skipping {pos['token_mint']}: {exc}")

    # Sort by |unrealized_pnl_sol| descending
    pnl_results.sort(key=lambda x: abs(x["unrealized_pnl_sol"]), reverse=True)

    total_pnl = sum(p["unrealized_pnl_sol"] for p in pnl_results)

    return {
        "wallet": wallet,
        "positions": pnl_results,
        "total_unrealized_pnl_sol": total_pnl,
    }


# ---------------------------------------------------------------------------
# Realized PnL helper (used inside SELL alert formatting)
# ---------------------------------------------------------------------------

def compute_sell_pnl(
    avg_cost_per_token: float,
    amount_sold: float,
    sol_received: float,
) -> tuple[float, float]:
    """
    Compute realized PnL for a sell event.

    Args:
        avg_cost_per_token: Average cost basis per token in SOL.
        amount_sold:        Number of tokens sold (human-readable).
        sol_received:       SOL received for the sell.

    Returns:
        (sell_pnl_sol, sell_pnl_pct)
    """
    cost_basis = avg_cost_per_token * amount_sold
    pnl_sol = sol_received - cost_basis
    pnl_pct = (pnl_sol / cost_basis * 100) if cost_basis > 0 else 0.0
    return pnl_sol, pnl_pct
