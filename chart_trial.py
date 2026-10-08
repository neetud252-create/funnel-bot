"""Two lifetime preview signals. Verification is read from the existing users table."""
import secrets

LIMIT = 2
SCHEMA = """
CREATE TABLE IF NOT EXISTS chart_trial_slots (
 token TEXT PRIMARY KEY, tg_id BIGINT NOT NULL, message_id BIGINT NOT NULL,
 status TEXT NOT NULL CHECK (status IN ('pending', 'used', 'released')),
 expires_at TIMESTAMPTZ NOT NULL,
 UNIQUE (tg_id, message_id)
);
CREATE INDEX IF NOT EXISTS chart_trial_user ON chart_trial_slots(tg_id);
"""


async def _status(conn, tg_id):
    verified = bool(await conn.fetchval('SELECT verified FROM users WHERE tg_id=$1', tg_id))
    used = await conn.fetchval("""SELECT COUNT(*) FROM chart_trial_slots WHERE tg_id=$1
        AND (status='used' OR (status='pending' AND expires_at > clock_timestamp()))""", tg_id)
    return verified, max(0, LIMIT - used)


async def status(pool, tg_id):
    async with pool.acquire() as conn:
        return await _status(conn, tg_id)


async def reserve(pool, tg_id, message_id):
    """Atomically hold a slot across workers; pending work expires after a crash."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute('SELECT pg_advisory_xact_lock(746230902)')
            verified, left = await _status(conn, tg_id)
            if verified:
                return None, None
            if not left:
                return 'trial', None
            token = secrets.token_hex(16)
            saved = await conn.fetchval("""INSERT INTO chart_trial_slots
                (token, tg_id, message_id, status, expires_at)
                VALUES ($1,$2,$3,'pending',clock_timestamp()+INTERVAL '3 minutes')
                ON CONFLICT (tg_id,message_id) DO NOTHING RETURNING token""", token, tg_id, message_id)
            return (None, saved) if saved else ('busy', None)


async def consume(pool, token):
    """Commit before sending. An uncertain Telegram delivery must not allow a third signal."""
    if token is None:  # Already verified at reservation.
        return True
    async with pool.acquire() as conn:
        return bool(await conn.fetchval("""UPDATE chart_trial_slots SET status='used'
            WHERE token=$1 AND status='pending' AND expires_at > clock_timestamp()
            RETURNING token""", token))


async def release(pool, token, *, delivery_rejected=False):
    if token is None:
        return
    async with pool.acquire() as conn:
        await conn.execute("""UPDATE chart_trial_slots SET status='released'
            WHERE token=$1 AND (status='pending' OR $2)""", token, delivery_rejected)
