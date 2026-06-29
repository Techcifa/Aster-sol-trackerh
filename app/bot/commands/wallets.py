"""
app/bot/commands/wallets.py

Handler for the /wallets command.
Lists all wallets tracked by the user, including labels, latest balance from snapshots,
and the date/time they were added.
"""

from aiogram.types import Message
from app import database as db


async def list_wallets_handler(message: Message) -> None:
    """
    Handles /wallets
    """
    if not message.from_user:
        return

    telegram_id = message.from_user.id
    wallets = await db.get_wallets_for_user(telegram_id)

    if not wallets:
        await message.reply(
            "📋 <b>Your Tracked Wallets</b>\n\nYou are not tracking any wallets yet. "
            "Use <code>/add &lt;wallet&gt; [label]</code> to start tracking.",
            parse_mode="HTML"
        )
        return

    response = "📋 <b>Your Tracked Wallets</b>\n\n"
    for idx, w in enumerate(wallets, 1):
        label_text = f" {w['label']}" if w.get("label") else ""
        bal = w.get("latest_balance_sol")
        bal_val = float(bal) if bal is not None else 0.0

        response += (
            f"{idx}. <code>{w['wallet']}</code>{label_text}\n"
            f"   SOL: <b>{bal_val:.4f}</b>\n"
            f"   Added: {w['added_at']}\n\n"
        )

    await message.reply(response.strip(), parse_mode="HTML")
