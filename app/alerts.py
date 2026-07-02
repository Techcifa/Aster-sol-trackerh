"""
app/alerts.py

Formats alert messages using Telegram HTML parse mode.
Conforms directly to the user's custom layout.
All formatting functions are async to fetch market stats (live SOL price and total supply).
"""

from datetime import datetime, timezone
from app import database as db
from app import jupiter
from app import helius


def _get_wallet_display(wallet: str, label: str | None) -> str:
    """Return the label if set, otherwise return the short wallet address (e.g. ABCD...WXYZ)."""
    if label:
        return label
    if len(wallet) >= 10:
        return f"{wallet[:6]}...{wallet[-4:]}"
    return wallet


def _format_elapsed_time(first_buy_at_iso: str | None) -> str:
    """Computes the elapsed time since first_buy_at (e.g., '1h 42m')."""
    if not first_buy_at_iso:
        return "New"
    try:
        first_buy = datetime.fromisoformat(first_buy_at_iso)
        delta = datetime.now(timezone.utc) - first_buy
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return "0m"
        
        minutes = (seconds // 60) % 60
        hours = (seconds // 3600) % 24
        days = seconds // 86400

        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0 or not parts:
            parts.append(f"{minutes}m")
        return " ".join(parts)
    except Exception:
        return "Unknown"


def _format_market_cap(mc: float | None) -> str:
    """Format market cap value cleanly (e.g. $3.65M, $120.5K). Returns 'N/A' when mc is None."""
    if mc is None:
        return "N/A"
    if mc >= 1_000_000_000:
        return f"${mc / 1_000_000_000:.2f}B"
    elif mc >= 1_000_000:
        return f"${mc / 1_000_000:.2f}M"
    elif mc >= 1_000:
        return f"${mc / 1_000:.2f}K"
    else:
        return f"${mc:.2f}"


def _format_usd_price(price: float) -> str:
    """Dynamic decimals formatting based on price scale."""
    if price >= 1.0:
        return f"{price:,.2f}"
    elif price >= 0.01:
        return f"{price:.4f}"
    elif price >= 0.000001:
        return f"{price:.6f}"
    else:
        return f"{price:.8f}"


async def format_sol_transfer(event: dict, label: str | None) -> str:
    """
    💸 SOL Movement
    Wallet: <code>{label or short_wallet}</code>
    """
    wallet_display = _get_wallet_display(event["wallet"], label)
    is_out = event["direction"] == "OUT"
    direction_text = "▼ Sent" if is_out else "▲ Received"
    to_from = "To" if is_out else "From"
    cp = event["counterparty"]
    cp_short = f"{cp[:6]}...{cp[-4:]}" if len(cp) >= 10 else cp

    return (
        f"💸 <a href=\"https://solscan.io/tx/{event['tx_sig']}\"><b>SOL Movement</b></a>\n"
        f"🔹 (<a href=\"https://solscan.io/account/{event['wallet']}?exclude_amount_zero=true&remove_spam=true#transfers\">{wallet_display}</a>)\n\n"
        f"{direction_text} <b>{event['amount_sol']:.4f} SOL</b>\n"
        f"{to_from}: <code>{cp_short}</code>\n"
        f"Balance: <b>{event['new_balance']:.4f} SOL</b> ({event['delta']:+.4f})"
    )


async def format_token_transfer(event: dict, label: str | None) -> str:
    """
    📦 Token Transfer
    """
    wallet_display = _get_wallet_display(event["wallet"], label)
    is_out = event["direction"] == "OUT"
    direction_text = "▼ Sent" if is_out else "▲ Received"
    to_from = "To" if is_out else "From"
    cp = event["counterparty"]
    cp_short = f"{cp[:6]}...{cp[-4:]}" if len(cp) >= 10 else cp

    return (
        f"📦 <a href=\"https://solscan.io/tx/{event['tx_sig']}\"><b>Token Transfer</b></a>\n"
        f"🔹 (<a href=\"https://solscan.io/account/{event['wallet']}?exclude_amount_zero=true&remove_spam=true#transfers\">{wallet_display}</a>)\n\n"
        f"{direction_text} <b>{event['amount']:,} {event['token_symbol']}</b>\n"
        f"{to_from}: <code>{cp_short}</code>"
    )


async def format_swap(event: dict, label: str | None) -> str:
    """
    Dispatches to the correct swap alert formatter based on swap_type.
    """
    swap_type = event.get("swap_type")
    if swap_type == "BUY":
        return await format_swap_buy(event, label)
    elif swap_type == "SELL":
        return await format_swap_sell(event, label)
    elif swap_type == "TOKEN_SWAP":
        return await format_token_swap(event, label)
    return ""


async def format_swap_buy(event: dict, label: str | None) -> str:
    """
    🟢 BUY event formatted to user's layout.
    """
    wallet = event["wallet"]
    mint = event["token_out_mint"]
    symbol = event["token_out_symbol"]
    sol_amount = event["sol_amount"]
    token_amount = event["token_out_amount"]
    tx_sig = event["tx_sig"]
    source = event.get("source", "DEX")

    # Fetch live market metrics (trade execution price — used for display only)
    sol_price_usd = await jupiter.get_sol_price_in_usd()
    usd_value = sol_amount * sol_price_usd
    usd_price = usd_value / token_amount if token_amount > 0 else 0.0

    # Market Cap: use live pool price from Jupiter + confirmed on-chain supply.
    # We intentionally do NOT reuse usd_price (trade price) here because it
    # includes slippage/price-impact and varies per transaction.
    meta = await helius.get_token_metadata(mint)
    supply = meta.get("supply")          # None when Helius returned no supply
    pool_price_usd = await jupiter.get_usd_price(mint)  # None on failure
    if supply is not None and pool_price_usd is not None and pool_price_usd > 0:
        market_cap: float | None = supply * pool_price_usd
    else:
        market_cap = None

    # Position "Seen" calculation
    pos = await db.get_position(wallet, mint)
    first_buy_at = pos["first_buy_at"] if pos else None
    seen_time = _format_elapsed_time(first_buy_at)

    wallet_display = _get_wallet_display(wallet, label)
    
    # Emoji prefix: use 🆕🟢 if it's the first buy, 🟢 if existing position
    prefix = "🆕🟢" if event.get("is_first_buy") else "🟢"

    return (
        f"{prefix} <a href=\"https://solscan.io/tx/{tx_sig}\"><b>BUY {symbol}</b></a> on {source}\n"
        f"🔹 (<a href=\"https://solscan.io/account/{wallet}?exclude_amount_zero=true&remove_spam=true#transfers\">{wallet_display}</a>)\n\n"
        f"🔹<a href=\"https://solscan.io/account/{wallet}?exclude_amount_zero=true&remove_spam=true#transfers\">{wallet_display}</a> "
        f"swapped <b>{sol_amount:.2f} SOL</b> (<a href=\"https://solscan.io/token/So11111111111111111111111111111111111111112\">SOL</a>) "
        f"for <b>{token_amount:,.2f}</b> (${usd_value:,.2f}) "
        f"<a href=\"https://solscan.io/token/{mint}\">{symbol}</a> @${_format_usd_price(usd_price)}\n\n"
        f"CA: <code>{mint}</code>\n"
        f"🔗 #{symbol} | MC: {_format_market_cap(market_cap)} | Seen: {seen_time}"
    )


async def format_swap_sell(event: dict, label: str | None) -> str:
    """
    🔴 SELL event formatted to user's layout.
    """
    wallet = event["wallet"]
    mint = event["token_in_mint"]
    symbol = event["token_in_symbol"]
    sol_amount = event["sol_amount"]
    token_amount = event["token_in_amount"]
    tx_sig = event["tx_sig"]
    source = event.get("source", "DEX")

    # Fetch live market metrics (trade execution price — used for display only)
    sol_price_usd = await jupiter.get_sol_price_in_usd()
    usd_value = sol_amount * sol_price_usd
    usd_price = usd_value / token_amount if token_amount > 0 else 0.0

    # Market Cap: use live pool price from Jupiter + confirmed on-chain supply.
    meta = await helius.get_token_metadata(mint)
    supply = meta.get("supply")          # None when Helius returned no supply
    pool_price_usd = await jupiter.get_usd_price(mint)  # None on failure
    if supply is not None and pool_price_usd is not None and pool_price_usd > 0:
        market_cap: float | None = supply * pool_price_usd
    else:
        market_cap = None

    # Position "Seen" calculation
    pos = await db.get_position(wallet, mint)
    first_buy_at = pos["first_buy_at"] if pos else None
    seen_time = _format_elapsed_time(first_buy_at)

    wallet_display = _get_wallet_display(wallet, label)

    return (
        f"🔴 <a href=\"https://solscan.io/tx/{tx_sig}\"><b>SELL {symbol}</b></a> on {source}\n"
        f"🔹 (<a href=\"https://solscan.io/account/{wallet}?exclude_amount_zero=true&remove_spam=true#transfers\">{wallet_display}</a>)\n\n"
        f"🔹<a href=\"https://solscan.io/account/{wallet}?exclude_amount_zero=true&remove_spam=true#transfers\">{wallet_display}</a> "
        f"swapped <b>{token_amount:,.2f} {symbol}</b> (<a href=\"https://solscan.io/token/{mint}\">{symbol}</a>) "
        f"for <b>{sol_amount:.2f} SOL</b> (<a href=\"https://solscan.io/token/So11111111111111111111111111111111111111112\">SOL</a>) "
        f"@{_format_usd_price(usd_price)} (${usd_value:,.2f})\n\n"
        f"📈 PnL on sell: <b>{event['sell_pnl']:+.4f} SOL ({event['sell_pnl_pct']:+.1f}%)</b>\n"
        f"Remaining: <b>{event['tokens_remaining']:,.2f} {symbol}</b>\n\n"
        f"CA: <code>{mint}</code>\n"
        f"🔗 #{symbol} | MC: {_format_market_cap(market_cap)} | Seen: {seen_time}"
    )


async def format_token_swap(event: dict, label: str | None) -> str:
    """
    🔄 Token-to-Token swap alert.
    """
    wallet = event["wallet"]
    mint_in = event["token_in_mint"]
    symbol_in = event["token_in_symbol"]
    amount_in = event["token_in_amount"]
    mint_out = event["token_out_mint"]
    symbol_out = event["token_out_symbol"]
    amount_out = event["token_out_amount"]
    tx_sig = event["tx_sig"]
    source = event.get("source", "DEX")

    # Position "Seen" calculation
    pos = await db.get_position(wallet, mint_out)
    first_buy_at = pos["first_buy_at"] if pos else None
    seen_time = _format_elapsed_time(first_buy_at)

    wallet_display = _get_wallet_display(wallet, label)

    return (
        f"🔄 <a href=\"https://solscan.io/tx/{tx_sig}\"><b>SWAP {symbol_in} ➔ {symbol_out}</b></a> on {source}\n"
        f"🔹 (<a href=\"https://solscan.io/account/{wallet}?exclude_amount_zero=true&remove_spam=true#transfers\">{wallet_display}</a>)\n\n"
        f"🔹<a href=\"https://solscan.io/account/{wallet}?exclude_amount_zero=true&remove_spam=true#transfers\">{wallet_display}</a> "
        f"swapped <b>{amount_in:,.2f} {symbol_in}</b> (<a href=\"https://solscan.io/token/{mint_in}\">{symbol_in}</a>) "
        f"for <b>{amount_out:,.2f} {symbol_out}</b> (<a href=\"https://solscan.io/token/{mint_out}\">{symbol_out}</a>)\n\n"
        f"CA: <code>{mint_out}</code>\n"
        f"🔗 #{symbol_out} | Seen: {seen_time}"
    )
