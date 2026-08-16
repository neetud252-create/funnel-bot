import os
import json
import asyncpg

DB_URL = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
pool: asyncpg.Pool | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id        BIGINT PRIMARY KEY,
    username     TEXT,
    uid          TEXT UNIQUE,
    verified     BOOLEAN DEFAULT FALSE,
    deposit      NUMERIC(12,2) DEFAULT 0,
    attempts     INT DEFAULT 0,
    ui_msg_id    BIGINT,
    last_checked TIMESTAMPTZ,
    created_at   TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE users ADD COLUMN IF NOT EXISTS album_ids TEXT;
-- Daily signal quota. The counter is stored per user so a restart cannot reset
-- it, and last_reset_date is what makes the rollover automatic: any row whose
-- date is not CURRENT_DATE is treated as 0 used and rewritten on first touch,
-- so no cron job or startup sweep is needed.
-- NOTE: CURRENT_DATE is the Postgres server's date (UTC on Railway), so "a new
-- day" means midnight UTC for every user regardless of their own timezone.
ALTER TABLE users ADD COLUMN IF NOT EXISTS signals_used_today INT NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_reset_date DATE;

CREATE TABLE IF NOT EXISTS traders (
    trader_id  TEXT PRIMARY KEY,
    registered BOOLEAN DEFAULT TRUE,
    deposit    NUMERIC(12,2) DEFAULT 0,
    last_event TEXT,
    updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS postbacks (
    id         BIGSERIAL PRIMARY KEY,
    raw        JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);
"""

async def connect():
    global pool
    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=5)
    async with pool.acquire() as c:
        await c.execute(SCHEMA)

async def touch_user(tg_id: int, username: str | None):
    async with pool.acquire() as c:
        await c.execute("""
            INSERT INTO users (tg_id, username) VALUES ($1,$2)
            ON CONFLICT (tg_id) DO UPDATE SET username=$2
        """, tg_id, username)

async def get_user(tg_id: int):
    async with pool.acquire() as c:
        return await c.fetchrow("SELECT * FROM users WHERE tg_id=$1", tg_id)

async def set_ui_msg(tg_id: int, msg_id: int):
    async with pool.acquire() as c:
        await c.execute("UPDATE users SET ui_msg_id=$1 WHERE tg_id=$2", msg_id, tg_id)
async def set_album(tg_id, ids):
    async with pool.acquire() as c:
        await c.execute("UPDATE users SET album_ids=$1 WHERE tg_id=$2", ids, tg_id)

# Interim UID capture. TODO(Group C): move to a dedicated traders table with
# verification state + postback linkage; users.uid is a stopgap store for now.
async def uid_owner(uid: str):
    async with pool.acquire() as c:
        row = await c.fetchrow("SELECT tg_id FROM users WHERE uid=$1", uid)
        return row["tg_id"] if row else None

async def save_uid_only(tg_id: int, uid: str):
    async with pool.acquire() as c:
        await c.execute("UPDATE users SET uid=$1 WHERE tg_id=$2", uid, tg_id)

# --- Group C: affiliate postbacks ---
async def log_postback(raw: dict):
    async with pool.acquire() as c:
        await c.execute("INSERT INTO postbacks (raw) VALUES ($1::jsonb)", json.dumps(raw))

async def upsert_trader(trader_id: str, event, amount):
    # Insert, or add the amount to the existing deposit (registration events
    # pass amount 0), and refresh last_event/updated_at.
    async with pool.acquire() as c:
        await c.execute("""
            INSERT INTO traders (trader_id, last_event, deposit, updated_at)
            VALUES ($1, $2, $3, now())
            ON CONFLICT (trader_id) DO UPDATE SET
                deposit    = traders.deposit + EXCLUDED.deposit,
                last_event = EXCLUDED.last_event,
                updated_at = now()
        """, trader_id, event, amount)

async def get_trader(trader_id: str):
    async with pool.acquire() as c:
        return await c.fetchrow("SELECT * FROM traders WHERE trader_id=$1", trader_id)

# --- Group F: panel-bot verification ---
async def cache_trader(trader_id: str, deposit, last_event="panel"):
    # Snapshot from a panel lookup: set deposit ABSOLUTELY (not additive like
    # the postback path), since the panel reports the running Sum of deposits.
    async with pool.acquire() as c:
        await c.execute("""
            INSERT INTO traders (trader_id, deposit, last_event, updated_at)
            VALUES ($1, $2, $3, now())
            ON CONFLICT (trader_id) DO UPDATE SET
                deposit    = EXCLUDED.deposit,
                last_event = EXCLUDED.last_event,
                updated_at = now()
        """, trader_id, deposit, last_event)

async def set_verified(tg_id: int, deposit):
    async with pool.acquire() as c:
        await c.execute(
            "UPDATE users SET verified=TRUE, deposit=$2, last_checked=now() WHERE tg_id=$1",
            tg_id, deposit)

# --- Daily signal quota -----------------------------------------------------
# Both helpers below roll the counter over themselves when last_reset_date is
# not today, so the reset happens automatically on the first read or write of a
# new day. Nothing schedules it.

# Kept as module constants so the test harness and db.py can't disagree on the
# statement text.
_ROLLOVER_SQL = """
    UPDATE users
       SET signals_used_today = 0, last_reset_date = CURRENT_DATE
     WHERE tg_id = $1
       AND last_reset_date IS DISTINCT FROM CURRENT_DATE
    RETURNING signals_used_today
"""

# One statement, so two taps arriving together can't both read "29 used" and
# both pass. The WHERE is the gate: it matches only while the user is under the
# limit (or the stored date is stale, which means a fresh day starting at 1).
_CONSUME_SQL = """
    UPDATE users
       SET signals_used_today = CASE
               WHEN last_reset_date IS DISTINCT FROM CURRENT_DATE THEN 1
               ELSE signals_used_today + 1
           END,
           last_reset_date = CURRENT_DATE
     WHERE tg_id = $1
       AND (last_reset_date IS DISTINCT FROM CURRENT_DATE
            OR signals_used_today < $2)
    RETURNING signals_used_today
"""

async def signal_state(tg_id: int, limit: int):
    # Read-side: (used, left) for today. Performs the rollover write when the
    # stored date is stale so the column on disk matches what the menu shows.
    async with pool.acquire() as c:
        row = await c.fetchrow(_ROLLOVER_SQL, tg_id)
        if row is None:
            row = await c.fetchrow(
                "SELECT signals_used_today FROM users WHERE tg_id=$1", tg_id)
        used = row["signals_used_today"] if row else 0
        return used, max(0, limit - used)

async def consume_signal(tg_id: int, limit: int):
    # Write-side: count one delivered signal. Returns (ok, used, left); ok is
    # False when the user is already at the cap, and nothing is incremented.
    async with pool.acquire() as c:
        row = await c.fetchrow(_CONSUME_SQL, tg_id, limit)
        if row is not None:
            used = row["signals_used_today"]
            return True, used, max(0, limit - used)
        # Refused: report the current count so the caller can show the menu
        # numbers without a second round trip.
        cur = await c.fetchrow(
            "SELECT signals_used_today FROM users WHERE tg_id=$1", tg_id)
        used = cur["signals_used_today"] if cur else limit
        return False, used, max(0, limit - used)

async def unverified_with_uid():
    async with pool.acquire() as c:
        return await c.fetch(
            "SELECT tg_id, uid FROM users WHERE uid IS NOT NULL AND verified=FALSE")
