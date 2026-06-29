"""
app/database.py

Handles all SQLite interactions via aiosqlite.

Design rules (from build spec):
- All async — no blocking calls.
- Use `async with aiosqlite.connect(...)` per operation — no global connections.
- DB_PATH always comes from settings, never hardcoded.
- os.makedirs is called before first connect so both Railway (/data already
  exists) and local dev (creates ./data/) work transparently.
"""

import os
import aiosqlite
from app.config import settings

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id  INTEGER PRIMARY KEY,
    username     TEXT,
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tracked_wallets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id  INTEGER NOT NULL,
    wallet       TEXT NOT NULL,
    label        TEXT,
    added_at     TEXT DEFAULT (datetime('now')),
    UNIQUE(telegram_id, wallet),
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
);

CREATE TABLE IF NOT EXISTS positions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet             TEXT NOT NULL,
    token_mint         TEXT NOT NULL,
    token_symbol       TEXT,
    token_name         TEXT,
    total_bought       REAL DEFAULT 0,
    total_spent_sol    REAL DEFAULT 0,
    total_sold         REAL DEFAULT 0,
    total_received_sol REAL DEFAULT 0,
    first_buy_at       TEXT,
    last_updated       TEXT DEFAULT (datetime('now')),
    UNIQUE(wallet, token_mint)
);

CREATE TABLE IF NOT EXISTS buy_lots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet          TEXT NOT NULL,
    token_mint      TEXT NOT NULL,
    amount          REAL NOT NULL,
    sol_spent       REAL NOT NULL,
    price_per_token REAL NOT NULL,
    tx_sig          TEXT NOT NULL,
    bought_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sol_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet      TEXT NOT NULL,
    balance_sol REAL NOT NULL,
    recorded_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS alerts_sent (
    tx_sig      TEXT NOT NULL,
    wallet      TEXT NOT NULL,
    alert_type  TEXT NOT NULL,
    sent_at     TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (tx_sig, wallet, alert_type)
);
"""

# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

async def init() -> None:
    """
    Create database directory if needed, then apply the full schema.
    Safe to call on every startup (all statements use CREATE TABLE IF NOT EXISTS).
    """
    db_dir = os.path.dirname(settings.DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.executescript(SCHEMA_SQL)
        await db.commit()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _connect():
    """Return an aiosqlite connection context manager."""
    return aiosqlite.connect(settings.DB_PATH)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

async def upsert_user(telegram_id: int, username: str | None) -> None:
    """
    Insert a new user or update their username on conflict.
    Called on every /start (and at the start of every command handler).
    """
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO users (telegram_id, username)
            VALUES (?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET username = excluded.username
            """,
            (telegram_id, username),
        )
        await db.commit()


async def get_user(telegram_id: int) -> dict | None:
    """Return the users row as a dict, or None if not found."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


# ---------------------------------------------------------------------------
# Tracked wallets
# ---------------------------------------------------------------------------

async def add_wallet(
    telegram_id: int,
    wallet: str,
    label: str | None = None,
) -> bool:
    """
    Insert a wallet for a user.
    Returns True if inserted, False if it was already tracked by this user.
    """
    async with _connect() as db:
        try:
            await db.execute(
                """
                INSERT INTO tracked_wallets (telegram_id, wallet, label)
                VALUES (?, ?, ?)
                """,
                (telegram_id, wallet, label),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def remove_wallet(telegram_id: int, wallet: str) -> bool:
    """
    Delete a user's tracked wallet.
    Returns True if a row was deleted, False if the wallet wasn't tracked.
    """
    async with _connect() as db:
        cursor = await db.execute(
            "DELETE FROM tracked_wallets WHERE telegram_id = ? AND wallet = ?",
            (telegram_id, wallet),
        )
        await db.commit()
        return cursor.rowcount > 0


async def is_wallet_tracked_by_user(telegram_id: int, wallet: str) -> bool:
    """True if this user is already tracking this wallet."""
    async with _connect() as db:
        async with db.execute(
            "SELECT 1 FROM tracked_wallets WHERE telegram_id = ? AND wallet = ?",
            (telegram_id, wallet),
        ) as cursor:
            return await cursor.fetchone() is not None


async def get_wallets_for_user(telegram_id: int) -> list[dict]:
    """
    Return all tracked wallets for a user, newest first, with the latest
    SOL snapshot balance joined in for the /wallets command display.
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT
                tw.wallet,
                tw.label,
                tw.added_at,
                (
                    SELECT balance_sol
                    FROM sol_snapshots
                    WHERE wallet = tw.wallet
                    ORDER BY recorded_at DESC
                    LIMIT 1
                ) AS latest_balance_sol
            FROM tracked_wallets tw
            WHERE tw.telegram_id = ?
            ORDER BY tw.added_at DESC
            """,
            (telegram_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_all_unique_wallets() -> list[str]:
    """
    Return every unique wallet address currently tracked by any user.
    Used to build the full address list when updating the Helius webhook.
    """
    async with _connect() as db:
        async with db.execute(
            "SELECT DISTINCT wallet FROM tracked_wallets"
        ) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]


async def get_users_tracking_wallet(wallet: str) -> list[int]:
    """
    Return all telegram_ids of users tracking the given wallet.
    Used for fan-out alert delivery.
    """
    async with _connect() as db:
        async with db.execute(
            "SELECT telegram_id FROM tracked_wallets WHERE wallet = ?",
            (wallet,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]


async def is_wallet_tracked_by_others(
    wallet: str, except_telegram_id: int
) -> bool:
    """
    True if at least one OTHER user (not except_telegram_id) tracks this wallet.
    Used by /remove to decide whether to pull the wallet from the Helius webhook.
    """
    async with _connect() as db:
        async with db.execute(
            """
            SELECT 1 FROM tracked_wallets
            WHERE wallet = ? AND telegram_id != ?
            LIMIT 1
            """,
            (wallet, except_telegram_id),
        ) as cursor:
            return await cursor.fetchone() is not None


async def get_wallet_label(telegram_id: int, wallet: str) -> str | None:
    """Return the label for a wallet, or None if not set."""
    async with _connect() as db:
        async with db.execute(
            "SELECT label FROM tracked_wallets WHERE telegram_id = ? AND wallet = ?",
            (telegram_id, wallet),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

async def get_position(wallet: str, token_mint: str) -> dict | None:
    """Return a position row as a dict, or None if no position exists yet."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM positions WHERE wallet = ? AND token_mint = ?",
            (wallet, token_mint),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_all_positions_for_wallet(wallet: str) -> list[dict]:
    """
    Return all positions for a wallet.
    Used by /pnl to iterate open positions.
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM positions WHERE wallet = ?",
            (wallet,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def upsert_position_buy(
    wallet: str,
    token_mint: str,
    token_symbol: str | None,
    token_name: str | None,
    amount_bought: float,
    sol_spent: float,
    first_buy_at: str | None = None,
) -> None:
    """
    Insert a new position or add to totals on an existing one (BUY side).
    Sets first_buy_at only when creating the row for the first time.
    """
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO positions
                (wallet, token_mint, token_symbol, token_name,
                 total_bought, total_spent_sol, first_buy_at, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')), datetime('now'))
            ON CONFLICT(wallet, token_mint) DO UPDATE SET
                token_symbol    = COALESCE(excluded.token_symbol, token_symbol),
                token_name      = COALESCE(excluded.token_name, token_name),
                total_bought    = total_bought + excluded.total_bought,
                total_spent_sol = total_spent_sol + excluded.total_spent_sol,
                last_updated    = datetime('now')
            """,
            (
                wallet,
                token_mint,
                token_symbol,
                token_name,
                amount_bought,
                sol_spent,
                first_buy_at,
            ),
        )
        await db.commit()


async def update_position_sell(
    wallet: str,
    token_mint: str,
    amount_sold: float,
    sol_received: float,
) -> None:
    """Update position totals after a SELL event."""
    async with _connect() as db:
        await db.execute(
            """
            UPDATE positions
            SET total_sold         = total_sold + ?,
                total_received_sol = total_received_sol + ?,
                last_updated       = datetime('now')
            WHERE wallet = ? AND token_mint = ?
            """,
            (amount_sold, sol_received, wallet, token_mint),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Buy lots
# ---------------------------------------------------------------------------

async def insert_buy_lot(
    wallet: str,
    token_mint: str,
    amount: float,
    sol_spent: float,
    price_per_token: float,
    tx_sig: str,
) -> None:
    """Record one buy lot for FIFO PnL calculations."""
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO buy_lots
                (wallet, token_mint, amount, sol_spent, price_per_token, tx_sig)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (wallet, token_mint, amount, sol_spent, price_per_token, tx_sig),
        )
        await db.commit()


async def get_buy_lots(wallet: str, token_mint: str) -> list[dict]:
    """Return all buy lots for a position, oldest first (FIFO order)."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM buy_lots
            WHERE wallet = ? AND token_mint = ?
            ORDER BY bought_at ASC
            """,
            (wallet, token_mint),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# SOL snapshots
# ---------------------------------------------------------------------------

async def insert_sol_snapshot(wallet: str, balance_sol: float) -> None:
    """Store a new SOL balance snapshot for a wallet."""
    async with _connect() as db:
        await db.execute(
            "INSERT INTO sol_snapshots (wallet, balance_sol) VALUES (?, ?)",
            (wallet, balance_sol),
        )
        await db.commit()


async def get_latest_sol_snapshot(wallet: str) -> dict | None:
    """
    Return the most recent SOL snapshot for a wallet as a dict,
    or None if no snapshot exists yet.
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM sol_snapshots
            WHERE wallet = ?
            ORDER BY recorded_at DESC
            LIMIT 1
            """,
            (wallet,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


# ---------------------------------------------------------------------------
# Alerts deduplication
# ---------------------------------------------------------------------------

async def is_alert_sent(tx_sig: str, wallet: str, alert_type: str) -> bool:
    """True if this (tx_sig, wallet, alert_type) combo has already been processed."""
    async with _connect() as db:
        async with db.execute(
            """
            SELECT 1 FROM alerts_sent
            WHERE tx_sig = ? AND wallet = ? AND alert_type = ?
            """,
            (tx_sig, wallet, alert_type),
        ) as cursor:
            return await cursor.fetchone() is not None


async def mark_alert_sent(tx_sig: str, wallet: str, alert_type: str) -> None:
    """
    Record that this alert has been sent.
    Uses INSERT OR IGNORE to handle any race-condition duplicates gracefully.
    """
    async with _connect() as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO alerts_sent (tx_sig, wallet, alert_type)
            VALUES (?, ?, ?)
            """,
            (tx_sig, wallet, alert_type),
        )
        await db.commit()
