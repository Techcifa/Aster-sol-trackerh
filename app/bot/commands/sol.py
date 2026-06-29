"""
app/bot/commands/sol.py

Handler for the /sol <wallet> command.
Fetches the live SOL balance via the Helius RPC, stores a new snapshot in the DB,
and displays the current balance.
"""

from datetime import datetime, timezone
from aiogram.types import Message
from app import database as db
from app import helius


async def wallet_sol_handler(message: Message) -> None:
    """
    Handles /sol <wallet>
    """
    if not message.text or not message.from_user:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply("❌ Usage: <code>/sol &lt;wallet&gt;</code>", parse_mode="HTML")
        return

    wallet = parts[1].strip()
    telegram_id = message.from_user.id

    # 1. Verify user tracks this wallet
    if not await db.is_wallet_tracked_by_user(telegram_id, wallet):
        await message.reply("❌ You are not tracking this wallet.")
        return

    # 2. Fetch latest snapshot (just to have it loaded, per spec)
    _ = await db.get_latest_sol_snapshot(wallet)

    # 3. Fetch live balance from RPC
    try:
        balance = await helius.get_sol_balance(wallet)
        # Store a new snapshot to keep database current
        await db.insert_sol_snapshot(wallet, balance)
    except Exception as exc:
        await message.reply(f"❌ Failed to fetch live SOL balance: {exc}")
        return

    # 4. Format and reply
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    response = (
        f"💰 <b>SOL Balance</b>\n"
        f"<code>{wallet}</code>\n\n"
        f"Balance: <b>{balance:.6f} SOL</b>\n"
        f"Last updated: {now_str}"
    )

    await message.reply(response, parse_mode="HTML")
