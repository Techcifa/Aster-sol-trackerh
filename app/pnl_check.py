from app import database as db

async def check_and_record_closure(wallet: str, token_mint: str):
    """
    Check if a position has been fully closed (total_sold >= total_bought)
    and record a finalized closure snapshot to the database if not already recorded.
    """
    position = await db.get_position(wallet, token_mint)
    if not position:
        return

    if position["total_bought"] > 0 and position["total_sold"] >= position["total_bought"]:
        # Verify it hasn't already been closed for this specific exit
        already_closed = await db.get_closed_position(wallet, token_mint, position["last_updated"])
        if already_closed:
            return

        realized_pnl_sol = position["total_received_sol"] - position["total_spent_sol"]
        realized_pnl_pct = (
            (realized_pnl_sol / position["total_spent_sol"] * 100)
            if position["total_spent_sol"] > 0
            else 0.0
        )

        await db.insert_closed_position(
            wallet=wallet,
            token_mint=token_mint,
            token_symbol=position["token_symbol"],
            total_bought=position["total_bought"],
            total_spent_sol=position["total_spent_sol"],
            total_sold=position["total_sold"],
            total_received_sol=position["total_received_sol"],
            realized_pnl_sol=realized_pnl_sol,
            realized_pnl_pct=realized_pnl_pct,
            opened_at=position["first_buy_at"],
            closed_at=position["last_updated"],  # Match closed_at exactly with last_updated to avoid duplicate records
        )
        print(f"[pnl_check] Recorded closed position for {wallet} / {token_mint}: PnL = {realized_pnl_sol:+.4f} SOL ({realized_pnl_pct:+.1f}%)")
