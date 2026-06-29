"""
app/alerts.py

Formats alert messages using Telegram HTML parse mode.
All helper functions take the event dictionary and the user's custom label (if any).
"""

def _get_wallet_display(wallet: str, label: str | None) -> str:
    """Return the label if set, otherwise return the short wallet address (e.g. ABCD...WXYZ)."""
    if label:
        return label
    if len(wallet) >= 10:
        return f"{wallet[:6]}...{wallet[-4:]}"
    return wallet


def format_sol_transfer(event: dict, label: str | None) -> str:
    """
    💸 SOL Movement
    Wallet: <code>{label or short_wallet}</code>

    {▼ Sent | ▲ Received} <b>{amount_sol:.4f} SOL</b>
    {"To" if OUT else "From"}: <code>{counterparty[:6]}...{counterparty[-4:]}</code>

    Balance: <b>{new_balance:.4f} SOL</b> ({delta:+.4f})
    🔗 <a href="https://solscan.io/tx/{tx_sig}">View tx</a>
    """
    wallet_display = _get_wallet_display(event["wallet"], label)
    is_out = event["direction"] == "OUT"
    direction_text = "▼ Sent" if is_out else "▲ Received"
    to_from = "To" if is_out else "From"
    cp = event["counterparty"]
    cp_short = f"{cp[:6]}...{cp[-4:]}" if len(cp) >= 10 else cp

    return (
        f"💸 <b>SOL Movement</b>\n"
        f"Wallet: <code>{wallet_display}</code>\n\n"
        f"{direction_text} <b>{event['amount_sol']:.4f} SOL</b>\n"
        f"{to_from}: <code>{cp_short}</code>\n\n"
        f"Balance: <b>{event['new_balance']:.4f} SOL</b> ({event['delta']:+.4f})\n"
        f"🔗 <a href=\"https://solscan.io/tx/{event['tx_sig']}\">View tx</a>"
    )


def format_token_transfer(event: dict, label: str | None) -> str:
    """
    📦 Token Transfer
    Wallet: <code>{label or short_wallet}</code>

    {▼ Sent | ▲ Received} <b>{amount} {symbol}</b>
    {"To" if OUT else "From"}: <code>{counterparty[:6]}...{counterparty[-4:]}</code>

    🔗 <a href="https://solscan.io/tx/{tx_sig}">View tx</a>
    """
    wallet_display = _get_wallet_display(event["wallet"], label)
    is_out = event["direction"] == "OUT"
    direction_text = "▼ Sent" if is_out else "▲ Received"
    to_from = "To" if is_out else "From"
    cp = event["counterparty"]
    cp_short = f"{cp[:6]}...{cp[-4:]}" if len(cp) >= 10 else cp

    return (
        f"📦 <b>Token Transfer</b>\n"
        f"Wallet: <code>{wallet_display}</code>\n\n"
        f"{direction_text} <b>{event['amount']} {event['token_symbol']}</b>\n"
        f"{to_from}: <code>{cp_short}</code>\n\n"
        f"🔗 <a href=\"https://solscan.io/tx/{event['tx_sig']}\">View tx</a>"
    )


def format_swap(event: dict, label: str | None) -> str:
    """
    Dispatches to the correct swap alert formatter based on swap_type.
    """
    swap_type = event.get("swap_type")
    if swap_type == "BUY":
        if event.get("is_first_buy"):
            return format_swap_new_buy(event, label)
        return format_swap_buy(event, label)
    elif swap_type == "SELL":
        return format_swap_sell(event, label)
    elif swap_type == "TOKEN_SWAP":
        return format_token_swap(event, label)
    return ""


def format_swap_new_buy(event: dict, label: str | None) -> str:
    """
    🆕🟢 NEW POSITION
    Wallet: <code>{label or short_wallet}</code>

    Aping into <b>${symbol}</b>
    Spent: <b>{sol_amount:.4f} SOL</b>
    Got: <b>{token_out_amount:,.0f} {symbol}</b>
    Price: <b>{price_per_token:.8f} SOL</b>

    CA: <code>{token_mint}</code>
    🔗 <a href="https://solscan.io/tx/{tx_sig}">View tx</a>
    """
    wallet_display = _get_wallet_display(event["wallet"], label)
    return (
        f"🆕🟢 <b>NEW POSITION</b>\n"
        f"Wallet: <code>{wallet_display}</code>\n\n"
        f"Aping into <b>${event['token_out_symbol']}</b>\n"
        f"Spent: <b>{event['sol_amount']:.4f} SOL</b>\n"
        f"Got: <b>{event['token_out_amount']:,.0f} {event['token_out_symbol']}</b>\n"
        f"Price: <b>{event['price_per_token']:.8f} SOL</b>\n\n"
        f"CA: <code>{event['token_out_mint']}</code>\n"
        f"🔗 <a href=\"https://solscan.io/tx/{event['tx_sig']}\">View tx</a>"
    )


def format_swap_buy(event: dict, label: str | None) -> str:
    """
    🟢 BUY
    Wallet: <code>{label or short_wallet}</code>

    Added to <b>${symbol}</b>
    Spent: <b>{sol_amount:.4f} SOL</b>
    Got: <b>{token_out_amount:,.0f} {symbol}</b>
    Avg Cost: <b>{avg_cost:.8f} SOL</b>

    🔗 <a href="https://solscan.io/tx/{tx_sig}">View tx</a>
    """
    wallet_display = _get_wallet_display(event["wallet"], label)
    return (
        f"🟢 <b>BUY</b>\n"
        f"Wallet: <code>{wallet_display}</code>\n\n"
        f"Added to <b>${event['token_out_symbol']}</b>\n"
        f"Spent: <b>{event['sol_amount']:.4f} SOL</b>\n"
        f"Got: <b>{event['token_out_amount']:,.0f} {event['token_out_symbol']}</b>\n"
        f"Avg Cost: <b>{event['avg_cost']:.8f} SOL</b>\n\n"
        f"🔗 <a href=\"https://solscan.io/tx/{event['tx_sig']}\">View tx</a>"
    )


def format_swap_sell(event: dict, label: str | None) -> str:
    """
    🔴 SELL
    Wallet: <code>{label or short_wallet}</code>

    Sold <b>{token_in_amount:,.0f} {symbol}</b>
    Got: <b>{sol_amount:.4f} SOL</b>

    PnL on this sell: <b>{sell_pnl:+.4f} SOL ({sell_pnl_pct:+.1f}%)</b>
    Remaining: <b>{tokens_remaining:,.0f} {symbol}</b>

    🔗 <a href="https://solscan.io/tx/{tx_sig}">View tx</a>
    """
    wallet_display = _get_wallet_display(event["wallet"], label)
    return (
        f"🔴 <b>SELL</b>\n"
        f"Wallet: <code>{wallet_display}</code>\n\n"
        f"Sold <b>{event['token_in_amount']:,.0f} {event['token_in_symbol']}</b>\n"
        f"Got: <b>{event['sol_amount']:.4f} SOL</b>\n\n"
        f"PnL on this sell: <b>{event['sell_pnl']:+.4f} SOL ({event['sell_pnl_pct']:+.1f}%)</b>\n"
        f"Remaining: <b>{event['tokens_remaining']:,.0f} {event['token_in_symbol']}</b>\n\n"
        f"🔗 <a href=\"https://solscan.io/tx/{event['tx_sig']}\">View tx</a>"
    )


def format_token_swap(event: dict, label: str | None) -> str:
    """
    🔄 Token-to-Token Swap
    Wallet: <code>{label or short_wallet}</code>

    Swapped <b>{token_in_amount:,.0f} {token_in_symbol}</b>
    For: <b>{token_out_amount:,.0f} {token_out_symbol}</b>

    🔗 <a href="https://solscan.io/tx/{tx_sig}">View tx</a>
    """
    wallet_display = _get_wallet_display(event["wallet"], label)
    return (
        f"🔄 <b>Token Swap</b>\n"
        f"Wallet: <code>{wallet_display}</code>\n\n"
        f"Swapped <b>{event['token_in_amount']:,.0f} {event['token_in_symbol']}</b>\n"
        f"For: <b>{event['token_out_amount']:,.0f} {event['token_out_symbol']}</b>\n\n"
        f"🔗 <a href=\"https://solscan.io/tx/{event['tx_sig']}\">View tx</a>"
    )
