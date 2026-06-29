"""
app/bot/commands/pnl.py

Handler for the /pnl <wallet> command.
Fetches all open positions for the wallet, calculates their unrealized profit/loss,
sorts them by absolute PnL value descending, and displays them with a total summary.
"""

from aiogram.types import Message
from app import database as db
from app import pnl


async def wallet_pnl_handler(message: Message) -> None:
    """
    Handles /pnl <wallet>
    """
    if not message.text or not message.from_user:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply("❌ Usage: <code>/pnl &lt;wallet&gt;</code>", parse_mode="HTML")
        return

    wallet = parts[1].strip()
    telegram_id = message.from_user.id

    # 1. Verify user tracks this wallet
    if not await db.is_wallet_tracked_by_user(telegram_id, wallet):
        await message.reply("❌ You are not tracking this wallet.")
        return

    # 2. Compute portfolio PnL
    try:
        portfolio = await pnl.compute_portfolio_pnl(wallet)
    except Exception as exc:
        await message.reply(f"❌ Failed to compute PnL: {exc}")
        return

    positions = portfolio["positions"]
    total_pnl = portfolio["total_unrealized_pnl_sol"]

    short_wallet = f"{wallet[:6]}...{wallet[-4:]}" if len(wallet) >= 10 else wallet

    if not positions:
        await message.reply(
            f"📊 <b>Open Positions</b>\n<code>{short_wallet}</code>\n\nNo open positions found for this wallet.",
            parse_mode="HTML"
        )
        return

    response = f"📊 <b>Open Positions</b>\n<code>{short_wallet}</code>\n\n"

    for idx, pos in enumerate(positions, 1):
        symbol = pos["token_symbol"]
        held = pos["tokens_held"]
        avg_cost = pos["avg_cost_sol"]
        current = pos["current_price_sol"]
        value = pos["current_value_sol"]
        unrealized = pos["unrealized_pnl_sol"]
        pct = pos["pnl_pct"]

        response += (
            f"{idx}. <b>${symbol}</b>\n"
            f"   Held: {held:,.0f}\n"
            f"   Avg cost: {avg_cost:.8f} SOL\n"
            f"   Current: {current:.8f} SOL\n"
            f"   Value: {value:.4f} SOL\n"
            f"   PnL: <b>{unrealized:+.4f} SOL ({pct:+.1f}%)</b>\n\n"
        )

    response += f"Total unrealized: <b>{total_pnl:+.4f} SOL</b>"

    await message.reply(response.strip(), parse_mode="HTML")
