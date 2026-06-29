"""
main.py

Main entrypoint for the Solana Wallet Tracker application.
Wires together FastAPI and the aiogram Telegram bot dispatcher, initializing
resources and launching the web server.
"""

import asyncio
from aiogram import Bot, Dispatcher
from fastapi import FastAPI
import uvicorn

from app.config import settings
from app import database as db
from app import helius
from app.bot.router import router as bot_router
from webhook.helius import router as helius_router
from webhook.telegram import router as telegram_router

# 1. Validate environment configuration early
try:
    settings.validate()
except ValueError as exc:
    print(f"[warning] Configuration validation failed: {exc}")
    print("[warning] Continuing boot; ensure env variables are set correctly in production.")

# 2. Initialize Telegram Bot & Dispatcher
bot_token = settings.TELEGRAM_BOT_TOKEN or "123456789:AABBCCDDEEFFggbbee"
bot = Bot(token=bot_token)
dp = Dispatcher()

# 3. Initialize FastAPI App
app = FastAPI(
    title="Solana Wallet Tracker",
    description="Real-time Solana wallet transfers and swaps tracking bot via Helius & Jupiter",
    version="1.0.0"
)

# Store instances in app state for access by HTTP route handlers
app.state.bot = bot
app.state.dp = dp

# 4. Register Webhook Routers
app.include_router(helius_router)
app.include_router(telegram_router)

# 5. Register Telegram Bot Router
dp.include_router(bot_router)


async def main():
    """
    Bootstrap lifecycle: database schema init, Helius registration,
    Telegram webhook registration, and FastAPI execution loop.
    """
    # Initialize SQLite database schema
    await db.init()

    # Ensure Helius webhook is registered
    if settings.HELIUS_API_KEY:
        try:
            await helius.ensure_webhook_registered()
        except Exception as exc:
            print(f"[main] Failed to ensure Helius webhook registration: {exc}")
    else:
        print("[main] HELIUS_API_KEY is not set. Skipping webhook setup.")

    # Register bot webhook URL with Telegram
    if settings.TELEGRAM_BOT_TOKEN and settings.WEBHOOK_BASE_URL:
        try:
            bot_webhook = settings.bot_webhook_url
            print(f"[main] Setting Telegram webhook to {bot_webhook} ...")
            await bot.set_webhook(bot_webhook)
        except Exception as exc:
            print(f"[main] Failed to set Telegram webhook: {exc}")
    else:
        print("[main] Telegram token or WEBHOOK_BASE_URL is not set. Skipping webhook registration.")

    # Configure and start FastAPI + Uvicorn server
    config = uvicorn.Config(app, host="0.0.0.0", port=8000)
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
