"""
app/bot/router.py

Registers and routes all Telegram command handlers.
"""

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from app import database as db
from app.bot.commands.add import add_wallet_handler
from app.bot.commands.remove import remove_wallet_handler
from app.bot.commands.wallets import list_wallets_handler
from app.bot.commands.pnl import wallet_pnl_handler
from app.bot.commands.sol import wallet_sol_handler

router = Router()


@router.message(CommandStart())
async def start_handler(message: Message) -> None:
    """
    Handles /start.
    Registers the user in the database and lists available commands.
    """
    if not message.from_user:
        return

    telegram_id = message.from_user.id
    username = message.from_user.username

    # Register user in DB
    await db.upsert_user(telegram_id, username)

    welcome_text = (
        "👋 <b>Welcome to the Solana Wallet Tracker Bot!</b>\n\n"
        "Monitor Solana wallets in real-time with instant alerts for SOL "
        "movements, SPL token transfers, swaps, and cost-basis PnL analytics.\n\n"
        "📋 <b>Available Commands:</b>\n"
        "• <code>/add &lt;wallet&gt; [label]</code> — Track a wallet with an optional label\n"
        "• <code>/remove &lt;wallet&gt;</code> — Stop tracking a wallet\n"
        "• <code>/wallets</code> — List all wallets you are tracking\n"
        "• <code>/sol &lt;wallet&gt;</code> — Get current live SOL balance\n"
        "• <code>/pnl &lt;wallet&gt;</code> — View unrealized PnL of open positions\n"
    )

    await message.reply(welcome_text, parse_mode="HTML")


# Centralized registration of command handlers
router.message.register(add_wallet_handler, Command("add"))
router.message.register(remove_wallet_handler, Command("remove"))
router.message.register(list_wallets_handler, Command("wallets"))
router.message.register(wallet_pnl_handler, Command("pnl"))
router.message.register(wallet_sol_handler, Command("sol"))
