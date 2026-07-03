"""
webhook/helius.py

FastAPI router for receiving transaction pushes from Helius webhooks.
Parses each transaction, handles database deduplication, and fans out
HTML alerts to all Telegram users tracking the respective wallets.
"""

import asyncio
from fastapi import APIRouter, Request, HTTPException
from app import database as db
from app import parser
from app import alerts
from app import safety
from app import helius

router = APIRouter()


@router.post("/helius")
async def helius_webhook(request: Request):
    """
    POST /helius

    Receives an array of enhanced transactions from Helius.
    Parses them, processes events, and alerts users.
    """
    try:
        transactions = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    # Safety: always process as a list
    if not isinstance(transactions, list):
        transactions = [transactions]

    bot = getattr(request.app.state, "bot", None)

    for tx in transactions:
        tx_sig = tx.get("signature", "unknown")
        try:
            events = await parser.parse_transaction(tx)
            for event in events:
                await process_event(event, bot)
        except Exception as exc:
            # Print warning but don't crash to ensure other transactions are processed
            print(f"[helius webhook] Failed parsing/processing tx {tx_sig}: {exc}")

    return {"ok": True}


async def process_event(event: dict, bot) -> None:
    """
    Deduplicates events, resolves the display label for each tracking user,
    and forwards the formatted HTML alert.
    """
    wallet = event.get("wallet")
    tx_sig = event.get("tx_sig")
    event_type = event.get("event_type")

    if not wallet or not tx_sig or not event_type:
        return

    # 1. Deduplication Check
    if await db.is_alert_sent(tx_sig, wallet, event_type):
        print(f"[helius webhook] Duplicate alert skipped: {tx_sig} | {wallet} | {event_type}")
        return

    # 2. Fan-out lookup
    telegram_ids = await db.get_users_tracking_wallet(wallet)
    if not telegram_ids:
        return

    # Pre-fetch first-buy metadata if this is a first buy
    safety_report = None
    balance_before = None
    launch_time = None
    is_first_buy = (
        event_type == "SWAP"
        and event.get("swap_type") == "BUY"
        and event.get("is_first_buy")
    )

    if is_first_buy:
        # Run concurrent lookups (5s timeout cap is handled inside the lookups)
        safety_report, raw_balance_before, launch_time = await asyncio.gather(
            safety.get_safety_report(event["token_out_mint"]),
            db.get_sol_balance_before(wallet, event["timestamp"]),
            helius.get_token_launch_time(event["token_out_mint"]),
        )

        # Fallback for balance_before if no snapshot exists yet
        if raw_balance_before is None:
            current_snapshot = await db.get_latest_sol_snapshot(wallet)
            current_balance = current_snapshot["balance_sol"] if current_snapshot else None
            balance_before = (current_balance or 0) + event["sol_amount"]
        else:
            balance_before = raw_balance_before

    # 3. Alert Formatting & Dispatch
    for tg_id in telegram_ids:
        label = await db.get_wallet_label(tg_id, wallet)

        if event_type == "SOL_TRANSFER":
            text = await alerts.format_sol_transfer(event, label)
        elif event_type == "TOKEN_TRANSFER":
            text = await alerts.format_token_transfer(event, label)
        elif event_type == "SWAP":
            if is_first_buy:
                text = alerts.format_new_position_alert(
                    event, safety_report, balance_before, launch_time, label
                )
            else:
                text = await alerts.format_swap(event, label)
        else:
            text = ""

        if bot and text:
            try:
                await bot.send_message(
                    chat_id=tg_id,
                    text=text,
                    parse_mode="HTML"
                )
            except Exception as bot_err:
                print(f"[helius webhook] Bot failed to send message to user {tg_id}: {bot_err}")

    # Mark as sent to prevent duplicate notifications from future retries/pushes
    await db.mark_alert_sent(tx_sig, wallet, event_type)
