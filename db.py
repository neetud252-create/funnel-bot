import os
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