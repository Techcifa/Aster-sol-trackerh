"""
webhook/telegram.py

FastAPI POST route to receive updates from the Telegram webhook.
Decodes updates and feeds them into the aiogram Dispatcher.
"""

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import APIRouter, Request, Response, status

router = APIRouter()


@router.post("/tg")
async def telegram_webhook(request: Request):
    """
    POST /tg

    Decodes the raw update from Telegram and forwards it to the aiogram dispatcher.
    """
    bot: Bot = getattr(request.app.state, "bot", None)
    dp: Dispatcher = getattr(request.app.state, "dp", None)

    if not bot or not dp:
        print("[tg webhook] Webhook route called but bot or dispatcher not in app state.")
        return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

    try:
        update_dict = await request.json()
    except Exception as exc:
        print(f"[tg webhook] Failed to parse update JSON: {exc}")
        return Response(status_code=status.HTTP_400_BAD_REQUEST)

    try:
        # Pydantic v2 validation context is required by aiogram for correct model parsing
        update = Update.model_validate(update_dict, context={"bot": bot})
        await dp.feed_webhook_update(bot, update)
    except Exception as exc:
        print(f"[tg webhook] Error processing or feeding update: {exc}")
        return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

    return Response(status_code=status.HTTP_200_OK)
