"""Admin drafts and a persistent, paced broadcast queue. Never modifies funnel FSM."""
import asyncio
import json
import logging
import secrets

from aiogram import F
from aiogram.filters import Command, Filter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.exceptions import (
    TelegramForbiddenError, TelegramRetryAfter, TelegramBadRequest,
    TelegramNetworkError, TelegramServerError,
)
import db
import config

SCHEMA = """
ALTER TABLE users ADD COLUMN IF NOT EXISTS broadcast_opt_out BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS broadcast_blocked BOOLEAN NOT NULL DEFAULT FALSE;
CREATE TABLE IF NOT EXISTS broadcasts (
 id TEXT PRIMARY KEY, admin_id BIGINT NOT NULL, source_chat BIGINT,
 source_message BIGINT, video_note BOOLEAN NOT NULL DEFAULT FALSE,
 audience TEXT NOT NULL DEFAULT 'all', button_kind TEXT NOT NULL DEFAULT 'none',
 status TEXT NOT NULL DEFAULT 'draft', created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS broadcast_one_draft ON broadcasts(admin_id)
 WHERE status='draft';
CREATE TABLE IF NOT EXISTS broadcast_deliveries (
 broadcast_id TEXT NOT NULL REFERENCES broadcasts(id), tg_id BIGINT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', PRIMARY KEY(broadcast_id,tg_id)
);
"""

AUDIENCES = {
    'all': ('All users', 'TRUE'),
    'new': ('Not registered', "uid IS NULL OR uid=''"),
    'waiting': ('UID submitted, not activated', 'uid IS NOT NULL AND uid<>\'\' AND NOT verified'),
    'active': ('Activated users', 'verified=TRUE'),
}
BUTTONS = {'none': 'No button', 'register': 'Register & Get Gift', 'open': 'Open Go+'}


async def query(sql, *args):
    async with db.pool.acquire() as conn:
        return await conn.fetch(sql, *args)


async def draft(admin):
    rows = await query("SELECT * FROM broadcasts WHERE admin_id=$1 AND status='draft'", admin)
    return rows[0] if rows else None


def controls(post):
    pid = post['id']
    rows = [[InlineKeyboardButton(
        text=('✓ ' if post['audience'] == key else '') + label,
        callback_data=f'bc:aud:{pid}:{key}')]
        for key, (label, _) in AUDIENCES.items()]
    rows.extend([[InlineKeyboardButton(
        text=('✓ ' if post['button_kind'] == key else '') + label,
        callback_data=f'bc:btn:{pid}:{key}')]
        for key, label in BUTTONS.items()])
    rows.append([InlineKeyboardButton(text='Preview', callback_data=f'bc:preview:{pid}'),
                 InlineKeyboardButton(text='Cancel', callback_data=f'bc:cancel:{pid}')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def recipient_keyboard(post, recipient):
    # These callbacks are separate from the existing funnel callbacks.
    rows = []
    if post['button_kind'] != 'none':
        rows.append([InlineKeyboardButton(text=BUTTONS[post['button_kind']],
                                         callback_data='bc:open')])
    if recipient in config.ADMIN_IDS:
        rows.append([InlineKeyboardButton(text='Stop broadcast messages', callback_data='bc:stop')])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def deliver(bot, post, recipient):
    if post['video_note']:
        # Telegram video notes cannot carry inline keyboards; send controls separately.
        await bot.copy_message(recipient, post['source_chat'], post['source_message'])
        await asyncio.sleep(1.05)
        markup = recipient_keyboard(post, recipient)
        if markup:
            await bot.send_message(recipient, 'Go+ update', reply_markup=markup)
    else:
        await bot.copy_message(recipient, post['source_chat'], post['source_message'],
                               reply_markup=recipient_keyboard(post, recipient))


async def count_recipients(post):
    predicate = AUDIENCES[post['audience']][1]
    rows = await query(f"SELECT count(*) AS n FROM users WHERE ({predicate}) "
                       "AND NOT broadcast_opt_out AND NOT broadcast_blocked")
    return rows[0]['n']


class DraftContent(Filter):
    def __init__(self, is_admin):
        self.is_admin = is_admin

    async def __call__(self, message):
        if message.chat.type != 'private' or not self.is_admin(message.from_user.id):
            return False
        if (message.text or '').startswith('/'):
            return False
        post = await draft(message.from_user.id)
        return {'broadcast_post': post} if post else False


def install(dp, is_admin, open_funnel):
    async def begin(m):
        if m.chat.type != 'private' or not is_admin(m.from_user.id):
            return
        if await draft(m.from_user.id):
            await m.answer('You already have a draft. Send new content to replace it, or /cancelbroadcast.')
            return
        created = await query("INSERT INTO broadcasts(id,admin_id) VALUES($1,$2) "
                              "ON CONFLICT DO NOTHING RETURNING id",
                              secrets.token_hex(6), m.from_user.id)
        if not created:
            await m.answer('A draft is already open. /cancelbroadcast to cancel it.')
            return
        await m.answer('Send one text, photo, video, round video, animation, or document. '
                       'Formatting and captions are preserved. Albums are not supported. '
                       'Nothing is sent to users until you preview and confirm.\n\n'
                       '/cancelbroadcast cancels your draft. /broadcaststatus shows delivery results.')

    async def cancel(m):
        if not is_admin(m.from_user.id):
            return
        await query("UPDATE broadcasts SET status='cancelled' WHERE admin_id=$1 AND status='draft'",
                    m.from_user.id)
        await m.answer('Broadcast draft cancelled.')

    async def status(m):
        if not is_admin(m.from_user.id):
            return
        rows = await query("SELECT * FROM broadcasts WHERE admin_id=$1 ORDER BY created_at DESC LIMIT 1",
                           m.from_user.id)
        if not rows:
            await m.answer('No broadcasts yet.')
            return
        await m.answer(await report(rows[0]))

    async def subscription(m):
        enabled = (m.text or '').split()[0].split('@')[0] == '/subscribe'
        await query('UPDATE users SET broadcast_opt_out=$2,broadcast_blocked=FALSE WHERE tg_id=$1',
                    m.from_user.id, not enabled)
        await m.answer('Broadcast messages enabled.' if enabled else
                       'Broadcast messages stopped. Bot access is unchanged. /subscribe to resume.')

    async def content(m, broadcast_post):
        if m.media_group_id or not (m.text or m.photo or m.video or m.video_note or m.animation or m.document):
            await m.answer('Please send one supported post, not an album or service message.')
            return
        rows = await query("UPDATE broadcasts SET source_chat=$2,source_message=$3,video_note=$4 "
                           "WHERE id=$1 AND status='draft' RETURNING *", broadcast_post['id'],
                           m.chat.id, m.message_id, bool(m.video_note))
        if rows:
            await m.answer('Post saved. Choose an audience and optional button, then Preview.',
                           reply_markup=controls(rows[0]))

    async def callback(cb, bot, state):
        parts = cb.data.split(':')
        action = parts[1]
        if action == 'stop':
            await query('UPDATE users SET broadcast_opt_out=TRUE WHERE tg_id=$1', cb.from_user.id)
            await cb.answer('Broadcast messages stopped', show_alert=True)
            return
        if action == 'open':
            await cb.answer()
            await open_funnel(cb, bot, state)
            return
        if not is_admin(cb.from_user.id) or len(parts) < 3:
            await cb.answer('Unavailable', show_alert=True)
            return
        post = await draft(cb.from_user.id)
        if not post or post['id'] != parts[2]:
            await cb.answer('This draft has expired or was already sent.', show_alert=True)
            return
        await cb.answer()
        if action == 'cancel':
            await query("UPDATE broadcasts SET status='cancelled' WHERE id=$1 AND status='draft'", post['id'])
            await bot.send_message(cb.from_user.id, 'Draft cancelled.')
        elif action in ('aud', 'btn') and len(parts) == 4:
            key = parts[3]
            allowed = AUDIENCES if action == 'aud' else BUTTONS
            if key not in allowed:
                return
            column = 'audience' if action == 'aud' else 'button_kind'
            rows = await query(f"UPDATE broadcasts SET {column}=$2 WHERE id=$1 AND status='draft' RETURNING *",
                               post['id'], key)
            if rows:
                try:
                    await cb.message.edit_reply_markup(reply_markup=controls(rows[0]))
                except TelegramBadRequest:
                    await bot.send_message(cb.from_user.id, 'Current draft settings:',
                                           reply_markup=controls(rows[0]))
        elif action == 'preview':
            if not post['source_message']:
                await bot.send_message(cb.from_user.id, 'Send the post first.')
                return
            try:
                await deliver(bot, post, cb.from_user.id)
            except TelegramBadRequest:
                await bot.send_message(cb.from_user.id, 'Cannot copy this post. Send a new supported post.')
                return
            count = await count_recipients(post)
            # Bind confirmation to the precise settings/content that were previewed.
            signature = preview_signature(post)
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=f'Send to {count} users',
                                     callback_data=f'bc:send:{post["id"]}:{signature}'),
                InlineKeyboardButton(text='Cancel', callback_data=f'bc:cancel:{post["id"]}')]])
            await bot.send_message(cb.from_user.id,
                f'PREVIEW ONLY\nAudience: {AUDIENCES[post["audience"]][0]}\n'
                f'Eligible recipients now: {count}\nButton: {BUTTONS[post["button_kind"]]}\n'
                'Confirm below to queue delivery. Recipient count may change before confirmation.',
                reply_markup=kb)
        elif action == 'send' and len(parts) == 4:
            if not post['source_message'] or parts[3] != preview_signature(post):
                await bot.send_message(cb.from_user.id, 'Draft changed. Preview it again before sending.')
                return
            queued = await enqueue(post)
            await bot.send_message(cb.from_user.id, 'Broadcast queued. /broadcaststatus for progress.'
                                   if queued else 'Already queued or cancelled.')

    dp.message.register(begin, Command('broadcast'))
    dp.message.register(cancel, Command('cancelbroadcast'))
    dp.message.register(status, Command('broadcaststatus'))
    dp.message.register(subscription, Command('unsubscribe', 'subscribe'))
    dp.message.register(content, DraftContent(is_admin))
    dp.callback_query.register(callback, F.data.startswith('bc:'))


def preview_signature(post):
    import hashlib
    data = [post['source_chat'], post['source_message'], post['audience'], post['button_kind']]
    return hashlib.sha256(json.dumps(data).encode()).hexdigest()[:12]


async def enqueue(post):
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            # Compare all previewed settings under the lock; concurrent edits cannot slip through.
            claimed = await conn.fetchrow("UPDATE broadcasts SET status='queued' WHERE id=$1 "
                "AND status='draft' AND source_message=$2 AND audience=$3 AND button_kind=$4 RETURNING id",
                post['id'], post['source_message'], post['audience'], post['button_kind'])
            if not claimed:
                return False
            predicate = AUDIENCES[post['audience']][1]
            await conn.execute(f"INSERT INTO broadcast_deliveries(broadcast_id,tg_id) "
                f"SELECT $1,tg_id FROM users WHERE ({predicate}) "
                "AND NOT broadcast_opt_out AND NOT broadcast_blocked", post['id'])
    return True


async def report(post):
    rows = await query('SELECT status,count(*) AS n FROM broadcast_deliveries '
                       'WHERE broadcast_id=$1 GROUP BY status', post['id'])
    counts = {r['status']: r['n'] for r in rows}
    return (f'Broadcast {post["id"]}: {post["status"]}\n'
            f'Delivered: {counts.get("sent",0)}\nBlocked: {counts.get("blocked",0)}\n'
            f'Failed: {counts.get("failed",0)}\nUncertain: {counts.get("uncertain",0)}\n'
            f'Skipped: {counts.get("skipped",0)}\n'
            f'Remaining: {counts.get("pending",0) + counts.get("sending",0)}')


async def run_queue(bot, conn):
    # A crash between Telegram acceptance and DB acknowledgement is ambiguous.
    # Do not resend such recipients automatically and risk duplicate marketing posts.
    await conn.execute("UPDATE broadcast_deliveries SET status='uncertain' WHERE status='sending'")
    while True:
        post = await conn.fetchrow("SELECT * FROM broadcasts WHERE status IN ('queued','running') "
                                   'ORDER BY created_at LIMIT 1')
        if not post:
            await asyncio.sleep(2)
            continue
        await conn.execute("UPDATE broadcasts SET status='running' WHERE id=$1", post['id'])
        rows = await conn.fetch("SELECT d.tg_id FROM broadcast_deliveries d "
                                "WHERE broadcast_id=$1 AND status='pending' ORDER BY tg_id LIMIT 100", post['id'])
        if not rows:
            await conn.execute("UPDATE broadcasts SET status='complete' WHERE id=$1", post['id'])
            try:
                await bot.send_message(post['admin_id'], await report(dict(post, status='complete')))
            except Exception:
                logging.exception('Broadcast report failed id=%s', post['id'])
            continue
        for row in rows:
            uid = row['tg_id']
            user = await conn.fetchrow('SELECT broadcast_opt_out,broadcast_blocked FROM users WHERE tg_id=$1', uid)
            result = 'skipped'
            if user and not user['broadcast_opt_out'] and not user['broadcast_blocked']:
                await conn.execute("UPDATE broadcast_deliveries SET status='sending' "
                                   'WHERE broadcast_id=$1 AND tg_id=$2', post['id'], uid)
                while True:
                    try:
                        await deliver(bot, post, uid)
                        result = 'sent'
                        break
                    except TelegramRetryAfter as exc:
                        if post['video_note']:
                            # First part may already have been accepted. Never duplicate it.
                            result = 'uncertain'
                            await asyncio.sleep(exc.retry_after + 1)
                            break
                        await asyncio.sleep(exc.retry_after + 1)
                    except TelegramForbiddenError:
                        result = 'blocked'
                        await conn.execute('UPDATE users SET broadcast_blocked=TRUE WHERE tg_id=$1', uid)
                        break
                    except TelegramBadRequest:
                        result = 'failed'
                        break
                    except (TelegramNetworkError, TelegramServerError):
                        result = 'uncertain'
                        break
                    except Exception:
                        logging.exception('Broadcast delivery failed id=%s', post['id'])
                        result = 'uncertain'
                        break
                # Below the free bulk rate, leaving capacity for normal bot interactions.
                await asyncio.sleep(0.15)
            await conn.execute('UPDATE broadcast_deliveries SET status=$3 '
                               'WHERE broadcast_id=$1 AND tg_id=$2', post['id'], uid, result)


async def worker(bot):
    while True:
        try:
            async with db.pool.acquire() as conn:
                locked = await conn.fetchval('SELECT pg_try_advisory_lock(8790198817)')
                if not locked:
                    await asyncio.sleep(5)
                    continue
                try:
                    await run_queue(bot, conn)
                finally:
                    await conn.execute('SELECT pg_advisory_unlock(8790198817)')
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception('Broadcast worker restarting')
            await asyncio.sleep(5)
