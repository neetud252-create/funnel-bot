import os
import json
import logging
import asyncpg

DB_URL = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
pool: asyncpg.Pool | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id        BIGINT PRIMARY KEY,
    username     TEXT,
    uid          TEXT,
    verified     BOOLEAN DEFAULT FALSE,
    deposit      NUMERIC(12,2) DEFAULT 0,
    attempts     INT DEFAULT 0,
    ui_msg_id    BIGINT,
    last_checked TIMESTAMPTZ,
    created_at   TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE users ADD COLUMN IF NOT EXISTS album_ids TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ;
-- Backfill for users verified before verified_at existed. set_verified wrote
-- last_checked at the moment of verification, so it is the best record we have.
-- Idempotent (only NULL rows), so it is safe to re-run on every boot.
UPDATE users SET verified_at = COALESCE(last_checked, created_at)
 WHERE verified = TRUE AND verified_at IS NULL;
-- Daily signal quota. The counter is stored per user so a restart cannot reset
-- it, and last_reset_date is what makes the rollover automatic: any row whose
-- date is not CURRENT_DATE is treated as 0 used and rewritten on first touch,
-- so no cron job or startup sweep is needed.
-- NOTE: CURRENT_DATE is the Postgres server's date (UTC on Railway), so "a new
-- day" means midnight UTC for every user regardless of their own timezone.
ALTER TABLE users ADD COLUMN IF NOT EXISTS signals_used_today INT NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_reset_date DATE;
-- message_id of the activation nudge, so it can be deleted once the user
-- verifies. Deliberately separate from ui_msg_id: the nudge is not a screen and
-- must not be touched by render()/wipe(). Persisted rather than held in memory
-- because the gap between the nudge and verification is often hours and spans
-- redeploys.
ALTER TABLE users ADD COLUMN IF NOT EXISTS nudge_msg_id BIGINT;

-- One Pocket Option uid may now be claimed by several telegram accounts: the
-- panel decides access, not our uniqueness. Databases created before this
-- change still carry the constraint from when uid was TEXT UNIQUE, and the
-- CREATE TABLE IF NOT EXISTS above will not remove it, so drop it explicitly.
-- users_uid_key is Postgres's default name for a column-level UNIQUE on
-- users(uid); connect() verifies afterwards that the drop actually took.
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_uid_key;
-- The unique index went with the constraint; uid lookups still want one.
CREATE INDEX IF NOT EXISTS users_uid_idx ON users (uid);

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
        # If the drop above did not apply (constraint created under a different
        # name), every second claimer of a uid would hit UniqueViolationError
        # inside save_uid_only and be shown MSG_UID_ERROR. Say so loudly at boot
        # rather than let it surface as a mystery halfway down the funnel.
        left = await c.fetch("""
            SELECT c.conname FROM pg_constraint c
            JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
            WHERE c.conrelid = 'users'::regclass AND c.contype = 'u' AND a.attname = 'uid'
        """)
        if left:
            logging.error("users.uid still has a UNIQUE constraint (%s) - shared uids "
                          "will fail with UniqueViolationError. Drop it manually: "
                          "ALTER TABLE users DROP CONSTRAINT <name>;",
                          ", ".join(r["conname"] for r in left))

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
async def set_nudge_msg(tg_id: int, msg_id):
    # msg_id None clears it, so a deleted (or undeletable) nudge is not retried.
    async with pool.acquire() as c:
        await c.execute("UPDATE users SET nudge_msg_id=$1 WHERE tg_id=$2", msg_id, tg_id)

async def set_album(tg_id, ids):
    async with pool.acquire() as c:
        await c.execute("UPDATE users SET album_ids=$1 WHERE tg_id=$2", ids, tg_id)

# Interim UID capture. TODO(Group C): move to a dedicated traders table with
# verification state + postback linkage; users.uid is a stopgap store for now.
# Unused since the uniqueness gate was removed from _capture_uid - kept because
# it is the natural "who owns this" helper. Use uid_owners() for the full list.
async def uid_owner(uid: str):
    async with pool.acquire() as c:
        row = await c.fetchrow("SELECT tg_id FROM users WHERE uid=$1", uid)
        return row["tg_id"] if row else None

async def save_uid_only(tg_id: int, uid: str):
    # Idempotent: tg_id is the PK, so a user re-sending their own uid rewrites
    # the one row rather than creating a second. Upsert rather than a bare
    # UPDATE so a user who somehow reaches capture without a row (never hit
    # /start) still gets one instead of silently saving nothing.
    async with pool.acquire() as c:
        await c.execute("""
            INSERT INTO users (tg_id, uid) VALUES ($1, $2)
            ON CONFLICT (tg_id) DO UPDATE SET uid = EXCLUDED.uid
        """, tg_id, uid)


async def uid_owners(uid: str):
    """Every telegram id currently holding this uid. Sharing is permitted - the
    panel decides access - but bot.py logs a WARNING when this returns >1."""
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT tg_id FROM users WHERE uid=$1 ORDER BY tg_id", uid)
        return [r["tg_id"] for r in rows]

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
    # The ONE place `verified` is ever set. Every grant path routes through here
    # (bot.py: the ACCESS branch, the TEST_MODE bypass, retry_worker), so no
    # route can hand out access without stamping the flag. verified_at uses
    # COALESCE so a re-verification keeps the original moment.
    async with pool.acquire() as c:
        await c.execute(
            "UPDATE users SET verified=TRUE, deposit=$2, last_checked=now(), "
            "verified_at=COALESCE(verified_at, now()) WHERE tg_id=$1",
            tg_id, deposit)

async def unverify(tg_id: int):
    # Testing helper behind /unverify. Drops the user back to the start of the
    # funnel; uid is deliberately kept so the same id can be re-sent at once.
    async with pool.acquire() as c:
        await c.execute("UPDATE users SET verified=FALSE, verified_at=NULL, "
                        "deposit=0, last_checked=NULL WHERE tg_id=$1", tg_id)

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
