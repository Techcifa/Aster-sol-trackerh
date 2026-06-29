"""
app/bot/commands/remove.py

Handler for the /remove <wallet> command.
Verifies the user tracks the wallet, deletes it from tracked_wallets, and removes it
from the Helius webhook if no other users are tracking it.
"""

from aiogram.types import Message
from app import database as db
from app import helius


async def remove_wallet_handler(message: Message) -> None:
    """
    Handles /remove <wallet>
    """
    if not message.text or not message.from_user:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply("❌ Usage: <code>/remove &lt;wallet&gt;</code>", parse_mode="HTML")
        return

    wallet = parts[1].strip()
    telegram_id = message.from_user.id

    # 1. Verify user tracks this wallet
    if not await db.is_wallet_tracked_by_user(telegram_id, wallet):
        await message.reply("❌ You are not tracking this wallet.")
        return

    # 2. Delete from DB
    deleted = await db.remove_wallet(telegram_id, wallet)
    if not deleted:
        await message.reply("❌ You are not tracking this wallet.")
        return

    # 3. Call Helius remove webhook if no other user tracks it
    try:
        is_tracked_by_others = await db.is_wallet_tracked_by_others(wallet, telegram_id)
        if not is_tracked_by_others:
            await helius.remove_wallet_from_webhook(wallet)
    except Exception as exc:
        print(f"[bot /remove] Failed to remove wallet from Helius webhook: {exc}")

    # 4. Reply
    short_wallet = f"{wallet[:6]}...{wallet[-4:]}" if len(wallet) >= 10 else wallet
    await message.reply(f"🗑️ Removed <code>{short_wallet}</code>", parse_mode="HTML")
