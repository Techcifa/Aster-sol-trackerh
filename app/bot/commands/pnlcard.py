from aiogram.types import Message, BufferedInputFile
from app import database as db
from app import pnl
from app import pnl_card
from datetime import datetime, timezone
import aiosqlite

def is_solana_address(val: str) -> bool:
    return len(val) == 44 and all(c in "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz" for c in val)

async def resolve_mint(wallet: str, token_mint_or_symbol: str) -> str | None:
    if is_solana_address(token_mint_or_symbol):
        return token_mint_or_symbol

    # Try resolving via positions first
    async with db._connect() as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT token_mint FROM positions WHERE wallet = ? AND UPPER(token_symbol) = ? LIMIT 1",
            (wallet, token_mint_or_symbol.upper()),
        )
        row = await cursor.fetchone()
        if row:
            return row["token_mint"]

        # Try resolving via closed_positions
        cursor = await conn.execute(
            "SELECT token_mint FROM closed_positions WHERE wallet = ? AND UPPER(token_symbol) = ? LIMIT 1",
            (wallet, token_mint_or_symbol.upper()),
        )
        row = await cursor.fetchone()
        if row:
            return row["token_mint"]

    return None

def format_duration(start_iso: str | None, end_iso: str | None) -> str:
    if not start_iso or not end_iso:
        return "unknown"
    try:
        start = datetime.fromisoformat(start_iso.replace(" ", "T"))
        end = datetime.fromisoformat(end_iso.replace(" ", "T"))
        delta = end - start
        seconds = int(delta.total_seconds())
        if seconds < 0:
            seconds = 0
        days = seconds // 86400
        hours = (seconds // 3600) % 24
        minutes = (seconds % 3600) // 60
        if days > 0:
            return f"{days}d {hours}h"
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
    except Exception:
        return "unknown"

async def get_latest_closed_position(wallet: str, token_mint: str) -> dict | None:
    async with db._connect() as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM closed_positions WHERE wallet = ? AND token_mint = ? ORDER BY closed_at DESC LIMIT 1",
            (wallet, token_mint),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

async def wallet_pnlcard_handler(message: Message) -> None:
    """
    Handles /pnlcard <wallet> <token_mint_or_symbol>
    """
    if not message.text or not message.from_user:
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.reply(
            "❌ Usage: <code>/pnlcard &lt;wallet&gt; &lt;token_mint_or_symbol&gt;</code>",
            parse_mode="HTML"
        )
        return

    wallet = parts[1].strip()
    token_mint_or_symbol = parts[2].strip()
    telegram_id = message.from_user.id

    # 1. Verify user tracks this wallet
    if not await db.is_wallet_tracked_by_user(telegram_id, wallet):
        await message.reply("❌ You are not tracking this wallet.")
        return

    # Resolve label
    label = await db.get_wallet_label(telegram_id, wallet)
    short_wallet = f"{wallet[:6]}...{wallet[-4:]}" if len(wallet) >= 10 else wallet
    wallet_label = label if label else short_wallet

    # 2. Resolve token mint
    token_mint = await resolve_mint(wallet, token_mint_or_symbol)
    if not token_mint:
        await message.reply(f"❌ Could not resolve token: {token_mint_or_symbol}")
        return

    # 3. Check closed positions first
    closed_pos = await get_latest_closed_position(wallet, token_mint)
    
    # 4. Check active positions if no closed position is found
    position = await db.get_position(wallet, token_mint)
    is_open = position and (position["total_bought"] - position["total_sold"] > 0)

    # Determine which position data to use: prefer open positions for active holdings
    if is_open:
        try:
            unrealized = await pnl.compute_unrealized_pnl(wallet, token_mint)
        except Exception as exc:
            await message.reply(f"❌ Failed to compute open PnL: {exc}")
            return
            
        now_iso = datetime.now(timezone.utc).isoformat()
        duration_str = format_duration(position["first_buy_at"], now_iso)
        
        card_data = {
            "token_symbol": unrealized["token_symbol"],
            "wallet_label": wallet_label,
            "is_closed": False,
            "pnl_sol": unrealized["unrealized_pnl_sol"],
            "pnl_pct": unrealized["pnl_pct"],
            "avg_cost_sol": unrealized["avg_cost_sol"],
            "current_or_exit_price_sol": unrealized["current_price_sol"],
            "holding_duration_str": duration_str,
            "held_amount": unrealized["tokens_held"],
        }
    elif closed_pos:
        avg_cost = closed_pos["total_spent_sol"] / closed_pos["total_bought"] if closed_pos["total_bought"] > 0 else 0.0
        exit_price = closed_pos["total_received_sol"] / closed_pos["total_sold"] if closed_pos["total_sold"] > 0 else 0.0
        duration_str = format_duration(closed_pos["opened_at"], closed_pos["closed_at"])
        
        card_data = {
            "token_symbol": closed_pos["token_symbol"] or token_mint[:6],
            "wallet_label": wallet_label,
            "is_closed": True,
            "pnl_sol": closed_pos["realized_pnl_sol"],
            "pnl_pct": closed_pos["realized_pnl_pct"],
            "avg_cost_sol": avg_cost,
            "current_or_exit_price_sol": exit_price,
            "holding_duration_str": duration_str,
            "held_amount": 0.0,
        }
    else:
        await message.reply(f"❌ No position found for {token_mint_or_symbol} in this wallet.")
        return

    # 5. Generate PnL Card image
    try:
        buffer = pnl_card.generate_pnl_card(card_data)
        photo_file = BufferedInputFile(buffer.read(), filename=f"pnl_{card_data['token_symbol']}.png")
        await message.reply_photo(photo=photo_file, caption=f"📊 PnL card for ${card_data['token_symbol']}")
    except Exception as exc:
        await message.reply(f"❌ Failed to generate PnL card: {exc}")
