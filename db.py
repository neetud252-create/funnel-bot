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

-- Access level. FALSE is Start, TRUE is Premium; the tier only selects which
-- limit is passed to the quota helpers below, it does not change how the
-- counter or the rollover work. Existing rows default to Start, so adding this
-- column changes nothing for anyone already in the table.
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_premium BOOLEAN NOT NULL DEFAULT FALSE;

-- In-game token balance. These are fictional credits earned and spent inside
-- the bot only: no money, no deposit and no affiliate event ever touches this
-- column. users.deposit above is the real trading-account figure and is a
-- separate thing entirely - nothing may move a value between the two.
-- Existing rows default to 0, so adding this changes nothing for anyone
-- already in the table.
ALTER TABLE users ADD COLUMN IF NOT EXISTS game_tokens INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS traders (
    trader_id  TEXT PRIMARY KEY,
    registered BOOLEAN DEFAULT TRUE,
    deposit    NUMERIC(12,2) DEFAULT 0,
    last_event TEXT,
    updated_at TIMESTAMPTZ DEFAULT now()
);
-- Telegram file_id cache. Keyed on the asset key; content_hash is the sha256 of
-- the file on disk at the moment that file_id was issued, so replacing artwork
-- invalidates the row automatically and no cache ever needs clearing by hand.
CREATE TABLE IF NOT EXISTS media_cache (
    asset_key    TEXT PRIMARY KEY,
    file_id      TEXT NOT NULL,
    content_hash TEXT,
    updated_at   TIMESTAMPTZ DEFAULT now()
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

# --- media file_id cache ---
async def load_media_cache():
    # ONE query at startup; bot.py fans the rows into its in-memory dicts.
    async with pool.acquire() as c:
        return await c.fetch("SELECT asset_key, file_id, content_hash FROM media_cache")

async def save_media_cache(asset_key: str, file_id: str, content_hash):
    async with pool.acquire() as c:
        await c.execute("""
            INSERT INTO media_cache (asset_key, file_id, content_hash, updated_at)
            VALUES ($1, $2, $3, now())
            ON CONFLICT (asset_key) DO UPDATE SET
                file_id      = EXCLUDED.file_id,
                content_hash = EXCLUDED.content_hash,
                updated_at   = now()
        """, asset_key, file_id, content_hash)

async def drop_media_cache(asset_key: str):
    # Called when Telegram rejects a stored file_id, so the next send re-uploads.
    async with pool.acquire() as c:
        await c.execute("DELETE FROM media_cache WHERE asset_key=$1", asset_key)

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

# --- Access level -----------------------------------------------------------
# The tier lives here rather than in an env list so a grant survives redeploys
# and is visible in the database. Mapping tier -> limit is config's job (see
# config.daily_limit), which is why nothing in this module reads config.

async def set_premium(tg_id: int, flag: bool):
    # Returns False when there is no such user, so the caller can say "unknown
    # tg_id" instead of silently reporting success for a typo'd ID.
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "UPDATE users SET is_premium=$2 WHERE tg_id=$1 RETURNING is_premium",
            tg_id, bool(flag))
        return row is not None

async def is_premium(tg_id: int):
    async with pool.acquire() as c:
        row = await c.fetchrow("SELECT is_premium FROM users WHERE tg_id=$1", tg_id)
        return bool(row["is_premium"]) if row else False

# --- In-game tokens and the Premium unlock ----------------------------------
# Fictional credits. Nothing here reads users.deposit, the traders table or any
# postback: a token balance is created only by an admin command and spent only
# by the unlock below.
#
# The unlock is ONE statement, which in Postgres is its own transaction. That is
# the whole concurrency story: the WHERE is the gate, so two taps arriving
# together cannot both see "100 tokens, not premium" and both spend it. The
# second statement finds is_premium already TRUE (or the balance already short)
# and matches no row, so it deducts nothing and returns None.
#
# A read-then-write pair - SELECT game_tokens, then UPDATE - would be the bug
# this is written to avoid: both callers would read 100, both would pass their
# own check, and the balance would go to -100 with one Premium granted twice.
_UNLOCK_PREMIUM_SQL = """
    UPDATE users
       SET game_tokens = game_tokens - $2,
           is_premium  = TRUE
     WHERE tg_id       = $1
       AND is_premium  = FALSE
       AND game_tokens >= $2
    RETURNING game_tokens
"""

async def unlock_premium(tg_id: int, cost: int):
    """Spend `cost` tokens to grant Premium, atomically.

    Returns (unlocked, balance, premium):
      unlocked - True only when THIS call performed the deduction
      balance  - the balance after the call, for the message to quote
      premium  - the tier after the call, so the caller never re-reads it
    """
    async with pool.acquire() as c:
        row = await c.fetchrow(_UNLOCK_PREMIUM_SQL, tg_id, cost)
        if row is not None:
            return True, row["game_tokens"], True
        # Refused. Report why from the same connection, so the message quotes
        # the balance that was actually in effect: either not enough tokens, or
        # Premium was already held (a second tap, or a concurrent one that lost).
        cur = await c.fetchrow(
            "SELECT game_tokens, is_premium FROM users WHERE tg_id=$1", tg_id)
        if cur is None:
            return False, 0, False
        return False, cur["game_tokens"], bool(cur["is_premium"])

async def game_tokens(tg_id: int):
    async with pool.acquire() as c:
        row = await c.fetchrow("SELECT game_tokens FROM users WHERE tg_id=$1", tg_id)
        return row["game_tokens"] if row else 0

# GREATEST keeps a balance from going negative when an admin subtracts more
# than the user holds. Single statement, so two grants cannot lose one another.
async def add_tokens(tg_id: int, amount: int):
    # Returns the new balance, or None when there is no such user - so the
    # command can say "unknown tg_id" instead of reporting a phantom success.
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "UPDATE users SET game_tokens = GREATEST(0, game_tokens + $2) "
            "WHERE tg_id=$1 RETURNING game_tokens", tg_id, amount)
        return row["game_tokens"] if row else None

async def set_tokens(tg_id: int, amount: int):
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "UPDATE users SET game_tokens = GREATEST(0, $2) "
            "WHERE tg_id=$1 RETURNING game_tokens", tg_id, amount)
        return row["game_tokens"] if row else None

# --- Development reset ------------------------------------------------------
# Puts ONE row back to the state a brand-new user's row is created in, so an
# admin can walk the funnel from the top without a second Telegram account.
#
# This is strictly wider than unverify() above, and the two are not
# interchangeable: unverify() undoes only the verification stamp and keeps the
# uid so the same account id can be re-sent immediately, which is what you want
# when re-testing the verification step alone. reset_user() additionally clears
# the uid, the daily quota, the tier and every message-tracking id, which is
# what you want when testing the funnel from the very first screen.

# Deliberately a single statement: in Postgres that is its own transaction, so
# the row can never be left half-reset (uid cleared but still verified, say) by
# a crash or a dropped connection partway through. Every column named here is
# set to the same value the SCHEMA defaults give a fresh row.
#
# last_checked and verified_at go with verified/deposit - set_verified writes
# all four together, so leaving either timestamp behind would describe a check
# this reset just undid. nudge_msg_id is cleared because the nudge it points at
# belongs to the funnel run being discarded; the id would otherwise outlive its
# message and the next verify would try to delete a stranger's message id.
# attempts is untouched: it is declared in SCHEMA but read and written nowhere.
_RESET_SQL = """
    UPDATE users
       SET verified           = FALSE,
           verified_at        = NULL,
           uid                = NULL,
           deposit            = 0,
           signals_used_today = 0,
           last_reset_date    = NULL,
           is_premium         = FALSE,
           ui_msg_id          = NULL,
           album_ids          = NULL,
           nudge_msg_id       = NULL,
           last_checked       = NULL
     WHERE tg_id = $1
    RETURNING tg_id
"""

async def reset_user(tg_id: int):
    # The WHERE is what keeps this to a single row - no caller can widen it.
    # Returns False when there is no such user, so a reset on an unknown tg_id
    # is reported rather than silently looking like it worked.
    async with pool.acquire() as c:
        row = await c.fetchrow(_RESET_SQL, tg_id)
        return row is not None

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
