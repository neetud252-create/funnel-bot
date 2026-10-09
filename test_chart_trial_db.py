"""Postgres integration; creates/drops a private test schema, never touches bot tables.

Set CHART_TEST_DATABASE_URL to run. No database credentials are logged.
"""
import asyncio
import os
import uuid
import asyncpg
import chart_trial as trial


async def main():
    url = os.getenv('CHART_TEST_DATABASE_URL')
    if not url:
        print('SKIP: set CHART_TEST_DATABASE_URL for isolated Postgres integration')
        return
    schema = 'test_chart_trial_' + uuid.uuid4().hex
    admin = await asyncpg.connect(url, timeout=20)
    pool = None
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        pool = await asyncpg.create_pool(url, min_size=1, max_size=4, timeout=20,
                                        server_settings={'search_path': schema})
        async with pool.acquire() as conn:
            await conn.execute('CREATE TABLE users (tg_id BIGINT PRIMARY KEY, verified BOOLEAN NOT NULL)')
            await conn.execute(trial.SCHEMA)
            await conn.execute('INSERT INTO users VALUES (11,false),(12,false),(13,true)')
        assert await trial.status(pool, 11) == (False, 2)
        # Five simultaneous requests can hold only two slots, even across connections.
        reserved = await asyncio.gather(*(trial.reserve(pool, 11, n) for n in range(5)))
        tokens = [token for blocked, token in reserved if blocked is None]
        assert len(tokens) == 2 and all(tokens)
        assert sum(blocked == 'trial' for blocked, _ in reserved) == 3
        assert await trial.status(pool, 11) == (False, 0)
        for token in tokens:
            assert await trial.consume(pool, token)
        # A released pending slot (invalid image/error) is available, but a used slot is not.
        blocked, free = await trial.reserve(pool, 12, 20)
        assert blocked is None
        await trial.release(pool, free)
        assert await trial.status(pool, 12) == (False, 2)
        assert (await trial.reserve(pool, 12, 20))[0] == 'busy'  # Replay suppressed.
        await trial.release(pool, tokens[0])
        assert await trial.status(pool, 11) == (False, 0)
        # Reopen connections (restart) and age rows across days: lifetime usage remains.
        async with pool.acquire() as conn:
            await conn.execute("UPDATE chart_trial_slots SET expires_at=clock_timestamp()-INTERVAL '2 days'")
        await pool.close()
        pool = await asyncpg.create_pool(url, min_size=1, max_size=4,
                                        server_settings={'search_path': schema})
        assert await trial.status(pool, 11) == (False, 0)
        assert (await trial.reserve(pool, 11, 99))[0] == 'trial'
        # Explicit Telegram rejection restores one slot; unknown delivery keeps it.
        await trial.release(pool, tokens[0], delivery_rejected=True)
        assert await trial.status(pool, 11) == (False, 1)
        _, pending = await trial.reserve(pool, 12, 21)
        async with pool.acquire() as conn:
            await conn.execute("UPDATE chart_trial_slots SET expires_at=clock_timestamp()-INTERVAL '1 second' WHERE token=$1", pending)
        assert not await trial.consume(pool, pending)
        assert await trial.status(pool, 12) == (False, 2)
        # Only stored verification bypasses lifetime gating; no quota is reset.
        assert await trial.reserve(pool, 13, 30) == (None, None)
        async with pool.acquire() as conn:
            await conn.execute('UPDATE users SET verified=true WHERE tg_id=11')
        assert await trial.reserve(pool, 11, 31) == (None, None)
        async with pool.acquire() as conn:
            await conn.execute('UPDATE users SET verified=false WHERE tg_id=11')
        assert await trial.status(pool, 11) == (False, 1)
        print('PASS: actual Postgres concurrency, two lifetime slots, replay, restart, expiry, refunds and verification')
    finally:
        if pool:
            await pool.close()
        # schema is generated locally from a fixed prefix + uuid hex, never user input.
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


if __name__ == '__main__':
    asyncio.run(main())
