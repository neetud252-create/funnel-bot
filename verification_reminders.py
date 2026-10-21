"""One persisted verification reminder per user, with opt-out and admin controls."""
import asyncio
import logging

from aiogram import F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.exceptions import (
    TelegramForbiddenError, TelegramRetryAfter, TelegramBadRequest,
    TelegramNetworkError, TelegramServerError,
)
import config
import db

TEXT = (
    "Hi! Your Go+ verification is still incomplete.\n\n"
    "If you would like to continue, tap Open verification below. "
    "If you already submitted a Pocket Option Trader ID, double-check it in "
    "your Pocket Option profile and submit the correct ID inside this bot.\n\n"
    "Need help? Use the Support option inside Go+. "
    "Never share your password or OTP.\n\n"
    "This is a one-time reminder. Tap Stop reminders or send /unsubscribe "
    "to stop reminder and broadcast messages."
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS verification_reminder_settings (
 id INTEGER PRIMARY KEY CHECK (id=1),
 enabled BOOLEAN NOT NULL DEFAULT TRUE,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO verification_reminder_settings(id) VALUES(1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS verification_reminder_deliveries (
 tg_id BIGINT PRIMARY KEY REFERENCES users(tg_id),
 status TEXT NOT NULL CHECK (status IN ('pending','sending','sent','blocked','failed','uncertain')),
 attempts INTEGER NOT NULL DEFAULT 1,
 attempted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 sent_at TIMESTAMPTZ,
 next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

# All current/future private-chat users, including those without a submitted UID.
# Reuse the existing broadcast opt-out/block flags; administrators are excluded.
ELIGIBLE = """
 u.verified IS FALSE AND u.tg_id>0
 AND NOT u.broadcast_opt_out AND NOT u.broadcast_blocked
 AND NOT (u.tg_id=ANY($1::bigint[]))
 AND u.created_at <= now()-interval '24 hours'
"""
DUE = """
 (d.tg_id IS NULL OR (d.status='pending' AND d.next_attempt_at<=now()))
"""
SELECT_DUE = f"""
 SELECT u.tg_id FROM users u
 LEFT JOIN verification_reminder_deliveries d USING(tg_id)
 WHERE {ELIGIBLE} AND {DUE}
 AND (SELECT enabled FROM verification_reminder_settings WHERE id=1)
 ORDER BY u.created_at,u.tg_id LIMIT 50
"""
CLAIM = f"""
 INSERT INTO verification_reminder_deliveries(tg_id,status)
 SELECT u.tg_id,'sending' FROM users u
 WHERE u.tg_id=$2 AND {ELIGIBLE}
 AND (SELECT enabled FROM verification_reminder_settings WHERE id=1)
 ON CONFLICT(tg_id) DO UPDATE SET status='sending',attempted_at=now(),
 attempts=verification_reminder_deliveries.attempts+1
 WHERE verification_reminder_deliveries.status='pending'
 AND verification_reminder_deliveries.next_attempt_at<=now()
 RETURNING attempts
"""
LOCK_ID = 8790198818


def keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Open verification', callback_data='vr:open')],
        [InlineKeyboardButton(text='Stop reminders', callback_data='vr:stop')],
    ])


async def report(conn):
    enabled = await conn.fetchval('SELECT enabled FROM verification_reminder_settings WHERE id=1')
    count = await conn.fetchval(f'SELECT count(*) FROM users u LEFT JOIN '
        f'verification_reminder_deliveries d USING(tg_id) WHERE {ELIGIBLE} AND {DUE}',
        list(config.ADMIN_IDS))
    rows = await conn.fetch('SELECT status,count(*) AS n FROM verification_reminder_deliveries GROUP BY status')
    counts = {row['status']: row['n'] for row in rows}
    return (f'Verification reminders: {"ON" if enabled else "OFF"}\n'
            'One reminder per user, at least 24 hours after joining.\n'
            f'Eligible now: {count}\nSent: {counts.get("sent", 0)}\n'
            f'Blocked: {counts.get("blocked", 0)}\nFailed: {counts.get("failed", 0)}\n'
            f'Uncertain (not repeated): {counts.get("uncertain", 0)}\n'
            '/remindersoff pauses delivery. /reminderson resumes.\n'
            '/verificationreminders shows this report and the message.')


def install(dp, is_admin, open_funnel):
    async def command(m):
        if m.chat.type != 'private' or not is_admin(m.from_user.id):
            return
        name = (m.text or '').split()[0].split('@')[0]
        async with db.pool.acquire() as conn:
            if name in ('/reminderson', '/remindersoff'):
                await conn.execute('UPDATE verification_reminder_settings SET enabled=$1,updated_at=now() WHERE id=1',
                                   name == '/reminderson')
            summary = await report(conn)
        await m.answer(summary + '\n\nMessage:\n' + TEXT)

    async def callback(cb, bot, state):
        if not cb.message or cb.message.chat.type != 'private':
            await cb.answer('Open Go+ in a private chat.', show_alert=True)
            return
        if cb.data == 'vr:stop':
            async with db.pool.acquire() as conn:
                await conn.execute('UPDATE users SET broadcast_opt_out=TRUE WHERE tg_id=$1', cb.from_user.id)
            await cb.answer('Reminders and broadcasts stopped. Bot access is unchanged.', show_alert=True)
        elif cb.data == 'vr:open':
            await cb.answer()
            # Existing route checks current verification and preserves access gates.
            await open_funnel(cb, bot, state)

    dp.message.register(command, Command('verificationreminders', 'reminderson', 'remindersoff'))
    dp.callback_query.register(callback, F.data.startswith('vr:'))


async def send_one(bot, conn, tg_id):
    # Admission is rechecked immediately before each send, including after a 429.
    claim = await conn.fetchrow(CLAIM, list(config.ADMIN_IDS), tg_id)
    if not claim:
        return 0
    try:
        await bot.send_message(tg_id, TEXT, reply_markup=keyboard())
        status = 'sent'
    except TelegramRetryAfter as exc:
        # Telegram explicitly rejected this send; retry later, never tight-loop.
        delay = max(1, int(exc.retry_after)) + 1
        status = 'pending' if claim['attempts'] < 5 else 'failed'
        await conn.execute("UPDATE verification_reminder_deliveries SET status=$2,"
                           "next_attempt_at=now()+$3::double precision*interval '1 second' WHERE tg_id=$1",
                           tg_id, status, delay)
        return delay
    except TelegramForbiddenError:
        status = 'blocked'
        await conn.execute('UPDATE users SET broadcast_blocked=TRUE WHERE tg_id=$1', tg_id)
    except TelegramBadRequest:
        status = 'failed'
    except (TelegramNetworkError, TelegramServerError):
        status = 'uncertain'
    except asyncio.CancelledError:
        # Leave 'sending' persisted; recovery must never repeat an ambiguous send.
        raise
    except Exception:
        logging.exception('Verification reminder send failed')
        status = 'uncertain'
    await conn.execute("UPDATE verification_reminder_deliveries SET status=$2,"
                       "sent_at=CASE WHEN $2='sent' THEN now() ELSE sent_at END WHERE tg_id=$1",
                       tg_id, status)
    return 1.1  # Under one send/second, leaving room for interactive replies.


async def run_queue(bot, conn):
    await conn.execute("UPDATE verification_reminder_deliveries SET status='uncertain' WHERE status='sending'")
    logging.info('Verification reminders ready; one per user after 24h; /verificationreminders')
    while True:
        rows = await conn.fetch(SELECT_DUE, list(config.ADMIN_IDS))
        if not rows:
            await asyncio.sleep(60)
            continue
        for row in rows:
            delay = await send_one(bot, conn, row['tg_id'])
            if delay:
                await asyncio.sleep(delay)
        logging.info('Verification reminders progress: %s', (await report(conn)).split('/remindersoff')[0].replace('\n', '; '))


async def worker(bot):
    while True:
        try:
            async with db.pool.acquire() as conn:
                if not await conn.fetchval('SELECT pg_try_advisory_lock($1::bigint)', LOCK_ID):
                    await asyncio.sleep(5)
                    continue
                try:
                    await run_queue(bot, conn)
                finally:
                    await conn.execute('SELECT pg_advisory_unlock($1::bigint)', LOCK_ID)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception('Verification reminder worker restarting')
            await asyncio.sleep(5)
