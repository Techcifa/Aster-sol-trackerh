"""
app/parser.py

Parses Helius enhanced transaction payloads into internal event dicts.

Supported event types  (as specified in the build prompt):
  - SOL_TRANSFER   — native SOL movement involving a tracked wallet
  - TOKEN_TRANSFER — SPL token movement (non-swap) involving a tracked wallet
  - SWAP           — Jupiter/DEX swap: BUY, SELL, or TOKEN_SWAP

Rules:
  - All amounts are converted to human-readable floats before being stored
    in the event dict (lamports ÷ 1e9 for SOL; raw ÷ 10^decimals for tokens).
  - For SWAP BUY: DB position + buy_lot are updated inside the parser.
  - For SWAP SELL: DB position totals are updated inside the parser.
  - SOL_TRANSFER also stores a new sol_snapshot and attaches the live
    balance + delta to the event dict (needed by the alert formatter).
  - If no tracked wallets are involved, returns an empty list.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app import database as db
from app import helius
from app.helius import get_token_metadata

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def parse_transaction(tx: dict) -> list[dict]:
    """
    Parse a single Helius enhanced transaction into 0-N internal event dicts.

    Args:
        tx: One element from the array Helius POSTs to the webhook endpoint.

    Returns:
        List of event dicts (may be empty if no tracked wallet is involved).
    """
    tracked_wallets: set[str] = set(await db.get_all_unique_wallets())
    if not tracked_wallets:
        return []

    tx_sig: str = tx.get("signature", "")
    timestamp: int = tx.get("timestamp", 0)
    tx_type: str = tx.get("type", "")
    fee_payer: str = tx.get("feePayer", "")
    events_block: dict = tx.get("events", {}) or {}
    swap_data: dict | None = events_block.get("swap")

    is_swap = tx_type == "SWAP" or swap_data is not None

    events: list[dict] = []

    if is_swap and swap_data:
        wallet = _find_swap_wallet(swap_data, fee_payer, tracked_wallets)
        if wallet:
            swap_events = await _parse_swap(
                swap_data, wallet, tx_sig, timestamp
            )
            events.extend(swap_events)
    else:
        # ---- SOL transfers ----
        for transfer in tx.get("nativeTransfers", []) or []:
            from_acc: str = transfer.get("fromUserAccount", "")
            to_acc: str = transfer.get("toUserAccount", "")
            lamports: int = transfer.get("amount", 0)
            amount_sol: float = lamports / 1_000_000_000

            for wallet in tracked_wallets:
                if from_acc == wallet:
                    event = await _build_sol_transfer_event(
                        wallet, "OUT", amount_sol, to_acc, tx_sig, timestamp
                    )
                    events.append(event)
                elif to_acc == wallet:
                    event = await _build_sol_transfer_event(
                        wallet, "IN", amount_sol, from_acc, tx_sig, timestamp
                    )
                    events.append(event)

        # ---- Token transfers (only when not a swap) ----
        for transfer in tx.get("tokenTransfers", []) or []:
            from_acc = transfer.get("fromUserAccount", "")
            to_acc = transfer.get("toUserAccount", "")
            mint: str = transfer.get("mint", "")
            raw_amount: float = float(transfer.get("tokenAmount", 0))
            decimals: int = int(transfer.get("decimals", 0))
            amount: float = raw_amount / (10 ** decimals) if decimals >= 0 else raw_amount

            meta = await get_token_metadata(mint)

            for wallet in tracked_wallets:
                if from_acc == wallet:
                    events.append(_build_token_transfer_event(
                        wallet, "OUT", mint, meta, amount, to_acc, tx_sig, timestamp
                    ))
                elif to_acc == wallet:
                    events.append(_build_token_transfer_event(
                        wallet, "IN", mint, meta, amount, from_acc, tx_sig, timestamp
                    ))

    return events



# ---------------------------------------------------------------------------
# SOL_TRANSFER builder
# ---------------------------------------------------------------------------

async def _build_sol_transfer_event(
    wallet: str,
    direction: str,
    amount_sol: float,
    counterparty: str,
    tx_sig: str,
    timestamp: int,
) -> dict:
    """
    Build a SOL_TRANSFER event dict and, as a side-effect, fetch the live
    SOL balance, store a new snapshot, and compute the delta vs. previous
    snapshot — both values are embedded in the event for the alert formatter.
    """
    # Fetch live balance and snapshot it
    try:
        new_balance = await helius.get_sol_balance(wallet)
    except Exception:  # noqa: BLE001
        new_balance = 0.0

    prev_snap = await db.get_latest_sol_snapshot(wallet)
    prev_balance: float = prev_snap["balance_sol"] if prev_snap else new_balance
    delta: float = new_balance - prev_balance

    await db.insert_sol_snapshot(wallet, new_balance)

    return {
        "event_type": "SOL_TRANSFER",
        "wallet": wallet,
        "direction": direction,
        "amount_sol": amount_sol,
        "counterparty": counterparty,
        "new_balance": new_balance,
        "delta": delta,
        "tx_sig": tx_sig,
        "timestamp": timestamp,
    }


# ---------------------------------------------------------------------------
# TOKEN_TRANSFER builder
# ---------------------------------------------------------------------------

def _build_token_transfer_event(
    wallet: str,
    direction: str,
    mint: str,
    meta: dict,
    amount: float,
    counterparty: str,
    tx_sig: str,
    timestamp: int,
) -> dict:
    return {
        "event_type": "TOKEN_TRANSFER",
        "wallet": wallet,
        "direction": direction,
        "token_mint": mint,
        "token_symbol": meta["symbol"],
        "token_name": meta["name"],
        "amount": amount,
        "counterparty": counterparty,
        "tx_sig": tx_sig,
        "timestamp": timestamp,
    }



# ---------------------------------------------------------------------------
# SWAP parser
# ---------------------------------------------------------------------------

def _find_swap_wallet(
    swap_data: dict, fee_payer: str, tracked_wallets: set[str]
) -> str | None:
    """
    Identify which tracked wallet is the actor in this swap.

    Priority:
      1. feePayer (most reliable — it signed the tx)
      2. account in nativeInput / nativeOutput
      3. userAccount in any tokenInput / tokenOutput
    """
    if fee_payer in tracked_wallets:
        return fee_payer

    native_in = swap_data.get("nativeInput") or {}
    if native_in.get("account") in tracked_wallets:
        return native_in["account"]

    native_out = swap_data.get("nativeOutput") or {}
    if native_out.get("account") in tracked_wallets:
        return native_out["account"]

    all_token_entries = list(swap_data.get("tokenInputs") or []) + list(
        swap_data.get("tokenOutputs") or []
    )
    for entry in all_token_entries:
        acct = entry.get("userAccount", "")
        if acct in tracked_wallets:
            return acct

    return None


async def _parse_swap(
    swap_data: dict,
    wallet: str,
    tx_sig: str,
    timestamp: int,
) -> list[dict]:
    """
    Determine swap direction relative to the tracked wallet and build event(s).

    BUY       : nativeInput (SOL out) + tokenOutputs (token in)
    SELL      : tokenInputs (token out) + nativeOutput (SOL in)
    TOKEN_SWAP: tokenInputs + tokenOutputs (no SOL side)
    """
    native_in = swap_data.get("nativeInput") or {}
    native_out = swap_data.get("nativeOutput") or {}
    token_inputs: list[dict] = list(swap_data.get("tokenInputs") or [])
    token_outputs: list[dict] = list(swap_data.get("tokenOutputs") or [])

    # Filter to entries belonging to our tracked wallet
    w_native_in = native_in if native_in.get("account") == wallet else {}
    w_native_out = native_out if native_out.get("account") == wallet else {}
    w_token_ins = [t for t in token_inputs if t.get("userAccount") == wallet]
    w_token_outs = [t for t in token_outputs if t.get("userAccount") == wallet]

    has_sol_in = bool(w_native_in)
    has_sol_out = bool(w_native_out)
    has_tok_in = bool(w_token_ins)
    has_tok_out = bool(w_token_outs)

    events: list[dict] = []

    if has_sol_in and has_tok_out:
        # ---- BUY ----
        sol_amount = _lamports_to_sol(w_native_in.get("amount", "0"))
        for tok_out in w_token_outs:
            ev = await _build_buy_event(
                wallet, sol_amount, tok_out, tx_sig, timestamp
            )
            events.append(ev)

    elif has_tok_in and has_sol_out:
        # ---- SELL ----
        sol_amount = _lamports_to_sol(w_native_out.get("amount", "0"))
        for tok_in in w_token_ins:
            ev = await _build_sell_event(
                wallet, sol_amount, tok_in, tx_sig, timestamp
            )
            events.append(ev)

    elif has_tok_in and has_tok_out:
        # ---- TOKEN_SWAP ----
        # Pair up inputs and outputs (typically 1:1)
        for tok_in, tok_out in zip(w_token_ins, w_token_outs):
            ev = await _build_token_swap_event(
                wallet, tok_in, tok_out, tx_sig, timestamp
            )
            events.append(ev)

    return events


async def _build_buy_event(
    wallet: str,
    sol_amount: float,
    tok_out: dict,
    tx_sig: str,
    timestamp: int,
) -> dict:
    """Build a SWAP/BUY event and persist position + buy_lot to DB."""
    mint, amount = _extract_token_amount(tok_out)
    meta = await get_token_metadata(mint)

    # first-buy check BEFORE inserting
    existing = await db.get_position(wallet, mint)
    is_first_buy = existing is None

    price_per_token = sol_amount / amount if amount > 0 else 0.0
    first_buy_at = _now_iso() if is_first_buy else None

    await db.upsert_position_buy(
        wallet, mint, meta["symbol"], meta["name"],
        amount, sol_amount, first_buy_at
    )
    await db.insert_buy_lot(
        wallet, mint, amount, sol_amount, price_per_token, tx_sig
    )

    # Fetch updated avg cost for the event dict
    position = await db.get_position(wallet, mint)
    avg_cost = (
        position["total_spent_sol"] / position["total_bought"]
        if position and position["total_bought"] > 0
        else price_per_token
    )

    return {
        "event_type": "SWAP",
        "wallet": wallet,
        "swap_type": "BUY",
        "token_in_mint": "SOL",
        "token_in_symbol": "SOL",
        "token_in_amount": sol_amount,
        "token_out_mint": mint,
        "token_out_symbol": meta["symbol"],
        "token_out_name": meta["name"],
        "token_out_amount": amount,
        "sol_amount": sol_amount,
        "price_per_token": price_per_token,
        "avg_cost": avg_cost,
        "is_first_buy": is_first_buy,
        "tx_sig": tx_sig,
        "timestamp": timestamp,
    }


async def _build_sell_event(
    wallet: str,
    sol_amount: float,
    tok_in: dict,
    tx_sig: str,
    timestamp: int,
) -> dict:
    """Build a SWAP/SELL event and persist updated position totals to DB."""
    mint, amount = _extract_token_amount(tok_in)
    meta = await get_token_metadata(mint)

    # Compute PnL before updating position
    position = await db.get_position(wallet, mint)
    sell_pnl, sell_pnl_pct, tokens_remaining = _compute_sell_pnl(
        position, amount, sol_amount
    )

    await db.update_position_sell(wallet, mint, amount, sol_amount)

    return {
        "event_type": "SWAP",
        "wallet": wallet,
        "swap_type": "SELL",
        "token_in_mint": mint,
        "token_in_symbol": meta["symbol"],
        "token_in_name": meta["name"],
        "token_in_amount": amount,
        "token_out_mint": "SOL",
        "token_out_symbol": "SOL",
        "token_out_amount": sol_amount,
        "sol_amount": sol_amount,
        "sell_pnl": sell_pnl,
        "sell_pnl_pct": sell_pnl_pct,
        "tokens_remaining": tokens_remaining,
        "is_first_buy": False,
        "tx_sig": tx_sig,
        "timestamp": timestamp,
    }


async def _build_token_swap_event(
    wallet: str,
    tok_in: dict,
    tok_out: dict,
    tx_sig: str,
    timestamp: int,
) -> dict:
    """Build a SWAP/TOKEN_SWAP event (token-to-token, no SOL side)."""
    mint_in, amount_in = _extract_token_amount(tok_in)
    mint_out, amount_out = _extract_token_amount(tok_out)

    meta_in = await get_token_metadata(mint_in)
    meta_out = await get_token_metadata(mint_out)

    # Sell side: update sold totals with 0 SOL received (no SOL in swap)
    await db.update_position_sell(wallet, mint_in, amount_in, 0.0)

    # Buy side
    existing = await db.get_position(wallet, mint_out)
    is_first_buy = existing is None
    first_buy_at = _now_iso() if is_first_buy else None

    await db.upsert_position_buy(
        wallet, mint_out, meta_out["symbol"], meta_out["name"],
        amount_out, 0.0, first_buy_at
    )
    await db.insert_buy_lot(wallet, mint_out, amount_out, 0.0, 0.0, tx_sig)

    return {
        "event_type": "SWAP",
        "wallet": wallet,
        "swap_type": "TOKEN_SWAP",
        "token_in_mint": mint_in,
        "token_in_symbol": meta_in["symbol"],
        "token_in_name": meta_in["name"],
        "token_in_amount": amount_in,
        "token_out_mint": mint_out,
        "token_out_symbol": meta_out["symbol"],
        "token_out_name": meta_out["name"],
        "token_out_amount": amount_out,
        "sol_amount": 0.0,
        "is_first_buy": is_first_buy,
        "tx_sig": tx_sig,
        "timestamp": timestamp,
    }


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _lamports_to_sol(raw: str | int) -> float:
    """Convert a lamports value (may arrive as a string from Helius) to SOL."""
    return int(raw) / 1_000_000_000


def _extract_token_amount(token_entry: dict) -> tuple[str, float]:
    """
    Extract mint address and human-readable token amount from a Helius
    tokenInput/tokenOutput entry.

    Helius shape:
        {
          "userAccount": "...",
          "mint": "...",
          "rawTokenAmount": {
            "tokenAmount": "1000000",
            "decimals": 6
          }
        }
    """
    mint: str = token_entry.get("mint", "")
    raw = token_entry.get("rawTokenAmount") or {}
    decimals: int = int(raw.get("decimals", 6))
    token_amount_raw: int = int(raw.get("tokenAmount", 0))
    amount: float = token_amount_raw / (10 ** decimals) if decimals >= 0 else float(token_amount_raw)
    return mint, amount


def _compute_sell_pnl(
    position: dict | None,
    amount_sold: float,
    sol_received: float,
) -> tuple[float, float, float]:
    """
    Compute realised PnL for a sell using average cost basis.

    Returns:
        (sell_pnl_sol, sell_pnl_pct, tokens_remaining_after_sell)
    """
    if not position or position["total_bought"] <= 0:
        return 0.0, 0.0, 0.0

    avg_cost = position["total_spent_sol"] / position["total_bought"]
    cost_basis_this_sell = avg_cost * amount_sold
    sell_pnl = sol_received - cost_basis_this_sell
    sell_pnl_pct = (sell_pnl / cost_basis_this_sell * 100) if cost_basis_this_sell > 0 else 0.0
    tokens_remaining = max(
        0.0,
        position["total_bought"] - position["total_sold"] - amount_sold
    )
    return sell_pnl, sell_pnl_pct, tokens_remaining


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
