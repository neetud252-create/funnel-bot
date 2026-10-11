"""Two lifetime previews, then the verified user's shared daily signal allowance."""
import secrets
import config

LIMIT = 2
SCHEMA = """
CREATE TABLE IF NOT EXISTS chart_trial_slots (
 token TEXT PRIMARY KEY, tg_id BIGINT NOT NULL, message_id BIGINT NOT NULL,
 status TEXT NOT NULL CHECK (status IN ('pending', 'used', 'released')),
 expires_at TIMESTAMPTZ NOT NULL,
 UNIQUE (tg_id, message_id)
);
CREATE INDEX IF NOT EXISTS chart_trial_user ON chart_trial_slots(tg_id);
-- NULL identifies lifetime previews, a date identifies verified daily usage.
-- Existing preview rows keep their meaning; no user counters are reset.
ALTER TABLE chart_trial_slots ADD COLUMN IF NOT EXISTS quota_date DATE;
"""


async def _status(conn, tg_id):
    user = await conn.fetchrow('SELECT *, CURRENT_DATE AS today FROM users WHERE tg_id=$1', tg_id)
    verified = bool(user and user['verified'])
    if verified:
        used = user['signals_used_today'] if user['last_reset_date'] == user['today'] else 0
        pending = await conn.fetchval("""SELECT COUNT(*) FROM chart_trial_slots
            WHERE tg_id=$1 AND quota_date=CURRENT_DATE AND status='pending'
              AND expires_at > clock_timestamp()""", tg_id)
        return True, max(0, config.daily_limit(user['is_premium']) - used - pending)
    used = await conn.fetchval("""SELECT COUNT(*) FROM chart_trial_slots WHERE tg_id=$1
        AND quota_date IS NULL
        AND (status='used' OR (status='pending' AND expires_at > clock_timestamp()))""", tg_id)
    return verified, max(0, LIMIT - used)


async def status(pool, tg_id):
    async with pool.acquire() as conn:
        return await _status(conn, tg_id)


async def reserve(pool, tg_id, message_id):
    """Atomically hold a slot across workers; pending work expires after a crash."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Lock the same user row as the manual signal counter. Different
            # users do not block each other; concurrent requests cannot overspend.
            user = await conn.fetchrow('SELECT tg_id FROM users WHERE tg_id=$1 FOR UPDATE', tg_id)
            if not user:
                return 'trial', None
            verified, left = await _status(conn, tg_id)
            if not left:
                return ('limit' if verified else 'trial'), None
            token = secrets.token_hex(16)
            saved = await conn.fetchval("""INSERT INTO chart_trial_slots
                (token, tg_id, message_id, status, expires_at, quota_date)
                VALUES ($1,$2,$3,'pending',clock_timestamp()+INTERVAL '3 minutes',
                        CASE WHEN $4 THEN CURRENT_DATE ELSE NULL END)
                ON CONFLICT (tg_id,message_id) DO NOTHING RETURNING token""", token, tg_id, message_id, verified)
            return (None, saved) if saved else ('busy', None)


async def consume(pool, token):
    """Commit before sending; uncertain delivery must not grant extra signals."""
    if token is None:
        return False
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Always lock user before slot, including in release().
            user = await conn.fetchrow("""SELECT *, CURRENT_DATE AS today FROM users
                WHERE tg_id=(SELECT tg_id FROM chart_trial_slots WHERE token=$1)
                FOR UPDATE""", token)
            slot = await conn.fetchrow("""SELECT * FROM chart_trial_slots
                WHERE token=$1 AND status='pending' AND expires_at > clock_timestamp()
                FOR UPDATE""", token)
            if not user or not slot:
                return False
            if slot['quota_date'] is not None and not user['verified']:
                return False
            if user['verified']:
                # Verification while an analysis is in flight moves that result
                # onto the daily quota. Midnight is rechecked at delivery too.
                from db import _CONSUME_SQL
                counted = await conn.fetchrow(_CONSUME_SQL, user['tg_id'],
                                               config.daily_limit(user['is_premium']))
                if not counted:
                    return False
            await conn.execute("""UPDATE chart_trial_slots SET status='used',
                quota_date=CASE WHEN $2 THEN CURRENT_DATE ELSE NULL END WHERE token=$1""",
                token, user['verified'])
            return True


async def release(pool, token, *, delivery_rejected=False):
    if token is None:
        return
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.fetchrow("""SELECT tg_id FROM users
                WHERE tg_id=(SELECT tg_id FROM chart_trial_slots WHERE token=$1)
                FOR UPDATE""", token)
            slot = await conn.fetchrow('SELECT * FROM chart_trial_slots WHERE token=$1 FOR UPDATE', token)
            if not slot or slot['status'] == 'released':
                return
            if slot['status'] == 'used':
                if not delivery_rejected:
                    return
                if slot['quota_date'] is not None:
                    # Never refund an old result against a new day's allowance.
                    await conn.execute("""UPDATE users
                        SET signals_used_today=GREATEST(0, signals_used_today-1)
                        WHERE tg_id=$1 AND last_reset_date=$2""", slot['tg_id'], slot['quota_date'])
            await conn.execute("UPDATE chart_trial_slots SET status='released' WHERE token=$1", token)
