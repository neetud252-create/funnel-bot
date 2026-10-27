"""Daily quota integration against an isolated PostgreSQL schema.

CHART_TEST_DATABASE_URL=... python test_chart_daily_db.py
Never invokes Telegram/Gemini or changes production users.
"""
import asyncio
import os
import uuid
from unittest.mock import patch

os.environ.setdefault('DATABASE_URL', 'postgresql://unused')
import asyncpg
import chart_analysis as api
import chart_trial as trial
import config
import db


async def main():
    url = os.getenv('CHART_TEST_DATABASE_URL')
    if not url:
        print('SKIP: set CHART_TEST_DATABASE_URL for quota integration')
        return
    schema = 'test_chart_daily_' + uuid.uuid4().hex
    admin = await asyncpg.connect(url, timeout=20)
    pool = None
    async def connect():
        return await asyncpg.create_pool(url, min_size=1, max_size=int(os.getenv('CHART_TEST_POOL_SIZE', '4')),
            server_settings={'search_path': schema}, statement_cache_size=0)
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        pool = await connect()
        db.pool = pool
        async with pool.acquire() as c:
            await c.execute('''CREATE TABLE users (
                tg_id BIGINT PRIMARY KEY, verified BOOLEAN NOT NULL,
                is_premium BOOLEAN NOT NULL DEFAULT FALSE,
                signals_used_today INT NOT NULL DEFAULT 0, last_reset_date DATE)''')
            await c.execute(trial.SCHEMA)
            await c.execute(api.SCHEMA)
            # Re-applying the additive migration preserves existing users.
            await c.execute(trial.SCHEMA)
            await c.execute('INSERT INTO users (tg_id,verified) VALUES (11,true),(12,true),(13,false)')
        with patch.object(config, 'START_DAILY_SIGNALS', 30), patch.object(config, 'PREMIUM_DAILY_SIGNALS', 70):
            assert await trial.status(pool, 11) == (True, 30)
            for n in range(30):
                blocked, token = await trial.reserve(pool, 11, n)
                assert blocked is None and token
                assert await trial.consume(pool, token)
                assert not await trial.consume(pool, token)  # Duplicate delivery cannot spend twice.
                await trial.release(pool, token)
                assert await db.signal_state(11, 30) == (n + 1, 29 - n)
            assert await trial.reserve(pool, 11, 31) == ('limit', None)
            assert not await trial.consume(pool, None)
            assert await db.signal_state(12, 30) == (0, 30)
            # Restart preserves the daily count.
            await pool.close()
            pool = await connect()
            db.pool = pool
            assert await trial.status(pool, 11) == (True, 0)
            # Rejected delivery refunds exactly once; uncertain sends stayed counted above.
            await trial.release(pool, token, delivery_rejected=True)
            await trial.release(pool, token, delivery_rejected=True)
            assert await db.signal_state(11, 30) == (29, 1)
            # Six simultaneous requests compete for one remaining slot.
            holds = await asyncio.gather(*(trial.reserve(pool, 11, 40+n) for n in range(6)))
            winners = [t for reason, t in holds if reason is None]
            assert len(winners) == 1
            assert sum(reason == 'limit' for reason, _ in holds) == 5
            # A manual signal can take the final slot while chart analysis runs.
            assert (await db.consume_signal(11, 30))[0]
            assert not await trial.consume(pool, winners[0])
            await trial.release(pool, winners[0])
            assert await db.signal_state(11, 30) == (30, 0)
            # New day resets automatically and an old refund cannot lower today's usage.
            async with pool.acquire() as c:
                await c.execute('UPDATE users SET last_reset_date=CURRENT_DATE-1 WHERE tg_id=11')
                await c.execute('UPDATE chart_trial_slots SET quota_date=CURRENT_DATE-1 WHERE tg_id=11')
            assert await trial.status(pool, 11) == (True, 30)
            _, fresh = await trial.reserve(pool, 11, 100)
            assert await trial.consume(pool, fresh)
            await trial.release(pool, holds[0][1], delivery_rejected=True)
            async with pool.acquire() as c:
                old = await c.fetchval("SELECT token FROM chart_trial_slots WHERE tg_id=11 AND status='used' AND quota_date<CURRENT_DATE LIMIT 1")
            await trial.release(pool, old, delivery_rejected=True)
            assert await db.signal_state(11, 30) == (1, 29)
            # Invalid/cancelled/expired work never increments usage; message replay is denied.
            _, pending = await trial.reserve(pool, 11, 101)
            await trial.release(pool, pending)
            assert (await trial.reserve(pool, 11, 101))[0] == 'busy'
            _, pending = await trial.reserve(pool, 11, 102)
            async with pool.acquire() as c:
                await c.execute("UPDATE chart_trial_slots SET expires_at=clock_timestamp()-INTERVAL '1 second' WHERE token=$1", pending)
            assert not await trial.consume(pool, pending)
            assert await trial.status(pool, 11) == (True, 29)
            # Premium retains its configured allowance and shares the counter.
            async with pool.acquire() as c:
                await c.execute('UPDATE users SET is_premium=true, signals_used_today=69, last_reset_date=CURRENT_DATE WHERE tg_id=12')
            _, premium = await trial.reserve(pool, 12, 1)
            assert await trial.consume(pool, premium)
            assert await trial.status(pool, 12) == (True, 0)
            assert await db.signal_state(12, 70) == (70, 0)
            # Verification in flight counts on the daily allowance, not lifetime trials.
            _, preview = await trial.reserve(pool, 13, 1)
            async with pool.acquire() as c:
                await c.execute('UPDATE users SET verified=true WHERE tg_id=13')
            assert await trial.consume(pool, preview)
            assert await db.signal_state(13, 30) == (1, 29)
            _, revoked = await trial.reserve(pool, 13, 2)
            async with pool.acquire() as c:
                await c.execute('UPDATE users SET verified=false WHERE tg_id=13')
            assert not await trial.consume(pool, revoked)
            await trial.release(pool, revoked)
            assert await trial.status(pool, 13) == (False, 1)
            # Zero allowance must not allow one signal on a stale date.
            async with pool.acquire() as c:
                await c.execute('UPDATE users SET last_reset_date=NULL WHERE tg_id=11')
            assert not (await db.consume_signal(11, 0))[0]
            with patch.object(config, 'START_DAILY_SIGNALS', 0):
                assert await trial.reserve(pool, 11, 200) == ('limit', None)
            # Real provider-reservation path no longer blocks verified users at five.
            async with pool.acquire() as c:
                await c.execute("INSERT INTO chart_analysis_usage VALUES (11,CURRENT_DATE,29,clock_timestamp()-INTERVAL '1 minute'),(0,CURRENT_DATE,29,clock_timestamp()-INTERVAL '1 minute')")
            assert await api.reserve(pool, 11) is None
            assert await api.reserve(pool, 11) == 'cooldown'
            assert await api.reserve(pool, 11, retry=True) is None
            async with pool.acquire() as c:
                assert await c.fetchval('SELECT requests FROM chart_analysis_usage WHERE tg_id=0') == 31
                assert await c.fetchval('SELECT requests FROM chart_analysis_usage WHERE tg_id=11') == 31
            assert await api.reserve(pool, 11) == 'cooldown'
            async with pool.acquire() as c:
                await c.execute('UPDATE chart_analysis_usage SET requests=$1 WHERE tg_id=0', api.GLOBAL_LIMIT)
            assert await api.reserve(pool, 11) == 'limit'
            assert await api.reserve(pool, 11, retry=True) == 'limit'
        print('PASS: daily 30/31, counters, restart, rollover, refunds, concurrent holds, shared manual quota, replay, expiry, Premium, verification, provider gates')
    finally:
        if pool:
            await pool.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


if __name__ == '__main__':
    asyncio.run(main())
