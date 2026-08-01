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
ALTER TABLE users ADD COLUMN IF NOT EXISTS album_ids TEXT;
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
