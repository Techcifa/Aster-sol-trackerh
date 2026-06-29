"""
app/config.py

Loads all environment variables from .env using python-dotenv and exposes
a single `settings` object used by every other module.

Never import API keys directly — always use settings.<FIELD>.
"""

import os
from dotenv import load_dotenv

# Load .env file (safe to call even if .env doesn't exist — it will just be a no-op)
load_dotenv()


class Settings:
    """
    Typed container for every env var defined in the build spec.
    Values are read once at import time from the process environment.
    """

    # ------------------------------------------------------------------ #
    # Telegram                                                             #
    # ------------------------------------------------------------------ #
    TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")

    # ------------------------------------------------------------------ #
    # Helius                                                               #
    # ------------------------------------------------------------------ #
    HELIUS_API_KEY: str = os.environ.get("HELIUS_API_KEY", "")

    # May be empty on first boot; populated after webhook registration
    HELIUS_WEBHOOK_ID: str = os.environ.get("HELIUS_WEBHOOK_ID", "")

    # ------------------------------------------------------------------ #
    # Public URL & webhook paths                                           #
    # ------------------------------------------------------------------ #
    # Base URL of the deployed service, e.g. https://sol-tracker.up.railway.app
    WEBHOOK_BASE_URL: str = os.environ.get("WEBHOOK_BASE_URL", "")

    # Path segments — note NO trailing slashes
    BOT_WEBHOOK_PATH: str = os.environ.get("BOT_WEBHOOK_PATH", "/tg")
    HELIUS_WEBHOOK_PATH: str = os.environ.get("HELIUS_WEBHOOK_PATH", "/helius")

    # ------------------------------------------------------------------ #
    # Database                                                             #
    # ------------------------------------------------------------------ #
    # Local dev default → ./data/tracker.db
    # Railway production → /data/tracker.db  (persistent volume)
    DB_PATH: str = os.environ.get("DB_PATH", "./data/tracker.db")

    # ------------------------------------------------------------------ #
    # Derived helpers (not in .env — computed from above)                  #
    # ------------------------------------------------------------------ #

    @property
    def helius_rpc_url(self) -> str:
        """Helius mainnet RPC endpoint used for DAS + getBalance calls."""
        return f"https://mainnet.helius-rpc.com/?api-key={self.HELIUS_API_KEY}"

    @property
    def helius_webhook_url(self) -> str:
        """Full public URL that Helius will POST transactions to."""
        return f"{self.WEBHOOK_BASE_URL}{self.HELIUS_WEBHOOK_PATH}"

    @property
    def bot_webhook_url(self) -> str:
        """Full public URL that Telegram will POST updates to."""
        return f"{self.WEBHOOK_BASE_URL}{self.BOT_WEBHOOK_PATH}"

    def validate(self) -> None:
        """
        Call once at startup to catch missing mandatory env vars early.
        Raises ValueError with a clear message listing every missing key.
        """
        missing: list[str] = []

        required = {
            "TELEGRAM_BOT_TOKEN": self.TELEGRAM_BOT_TOKEN,
            "HELIUS_API_KEY": self.HELIUS_API_KEY,
            "WEBHOOK_BASE_URL": self.WEBHOOK_BASE_URL,
        }

        for key, value in required.items():
            if not value:
                missing.append(key)

        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing)}. "
                "Please set them in your .env file or Railway environment."
            )


# Singleton — every module does: from app.config import settings
settings = Settings()
