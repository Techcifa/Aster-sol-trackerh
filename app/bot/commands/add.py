"""
app/bot/commands/add.py

Handler for the /add <wallet> [label] command.
Validates the address, updates the database, registers the wallet with the Helius webhook,
and records an initial SOL balance snapshot.
"""

from aiogram.types import Message
from app import database as db
from app import helius

BASE58_ALPHABET = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")


def is_valid_solana_address(address: str) -> bool:
    """Validate if the string is a valid base58 Solana address (32-44 chars)."""
    if not (32 <= len(address) <= 44):
        return False
    return all(c in BASE58_ALPHABET for c in address)


async def add_wallet_handler(message: Message) -> None:
    """
    Handles /add <wallet> [label]
    """
    if not message.text or not message.from_user:
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 2:
        await message.reply("❌ Usage: <code>/add &lt;wallet&gt; [label]</code>", parse_mode="HTML")
        return

    wallet = parts[1].strip()
    label = parts[2].strip() if len(parts) > 2 else None

    # 1. Validation
    if not is_valid_solana_address(wallet):
        await message.reply("❌ Invalid Solana wallet address. Must be a base58 string between 32 and 44 characters.")
        return

    telegram_id = message.from_user.id
    username = message.from_user.username

    # Ensure user is registered
    await db.upsert_user(telegram_id, username)

    # 2. Check if already tracking
    if await db.is_wallet_tracked_by_user(telegram_id, wallet):
        await message.reply("Already tracking.")
        return

    # 3. Insert into DB
    success = await db.add_wallet(telegram_id, wallet, label)
    if not success:
        await message.reply("Already tracking.")
        return

    # 4. Update Helius webhook
    try:
        await helius.add_wallet_to_webhook(wallet)
    except Exception as exc:
        print(f"[bot /add] Failed to add wallet to Helius webhook: {exc}")

    # 5. Fetch current SOL balance and snapshot
    try:
        balance = await helius.get_sol_balance(wallet)
        await db.insert_sol_snapshot(wallet, balance)
    except Exception as exc:
        print(f"[bot /add] Failed to fetch/store initial SOL snapshot: {exc}")

    # 6. Reply
    short_wallet = f"{wallet[:6]}...{wallet[-4:]}"
    await message.reply(
        f"✅ Now tracking <code>{short_wallet}</code>" + (f" ({label})" if label else ""),
        parse_mode="HTML"
    )
