"""Pocket IQ -> Elena Business connection, contextual help and durable reminders.

Only @elitecsupport may attach. A different chat/account cannot select another
customer by UID. AI conversation requires explicit customer opt-in.
"""
import asyncio
import logging
import os
import re
import json
import secrets
from html import escape
import weakref
from datetime import datetime, timezone, timedelta
from aiogram import F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, BufferedInputFile
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
import config
import db
import journey
import localization
import elena_copy as copy
import elena_store as store
import elena_ai as ai

USERNAME = 'elitecsupport'
BOT_USERNAME = 'goplusaitrader_bot'
LOCK_ID = 8790198820
_locks = weakref.WeakValueDictionary()
_check = None
_busy = lambda uid: False
_checking = set()


def checking(tg_id):
    return tg_id in _checking


def enabled():
    return os.getenv('ELENA_ENABLED', '1').lower() in ('1', 'true', 'yes', 'on')


def reason_for(user):
    if not user or user.get('verified'):
        return None
    status = user.get('access_check_status')
    if status == config.VERIFY_NEED_DEPOSIT:
        return 'deposit'
    if status == config.VERIFY_WRONG_LINK:
        return 'uid'
    if status == config.VERIFY_TEMPORARY:
        return 'temporary'
    if user.get('setup_stage') in ('register', 'existing_register', 'id'):
        return 'registration'
    if user.get('setup_stage') in ('help', 'referral_help', 'account_help'):
        return 'problem'
    return 'inactive'


def wait_seconds(reason):
    # Follow up on incorrect IDs after one minute; other incomplete setup
    # (including a confirmed missing qualifying deposit) gets thirty minutes.
    return 60 if reason == 'uid' else 1800


def link(action='home'):
    return f'https://t.me/{BOT_USERNAME}?start=elena_{action}'


def button(label, *, action=None, url=None):
    return InlineKeyboardButton(text=label, callback_data='elena:' + action if action else None, url=url)


def keyboard(reason='inactive', *, invitation=False):
    if invitation:
        rows = [[button('Chat with Elena', url=f'https://t.me/{USERNAME}')],
                [button('Continue Setup', url=link('setup'))]]
    else:
        choices = {
            'inactive': [('🎁 Try 2 Free Analyses', 'signals'), ('Continue Setup', 'account')],
            'registration': [('✅ I Created an Account — Check ID', 'uid'), ('📖 Guide', 'guide')],
            'uid': [('📍 Where Is My Trader ID?', 'uid'), ('🔄 Check Again', 'recheck')],
            'temporary': [('🔄 Check Again', 'recheck'), ('📖 Guide', 'guide')],
            'deposit': [('✅ I’ve Deposited — Check Again', 'recheck'), ('📖 Deposit Help', 'deposit')],
            'problem': [('👤 Account Help', 'account'), ('📊 Analysis Help', 'signals')],
            'ready': [('📊 Get Signals', 'signals'), ('📖 Guide', 'guide')],
        }.get(reason, [('Continue Setup', 'account')])
        rows = [[button(label, action=action)] for label, action in choices]
        rows.append([button('Talk to a person', action='human')])
    rows += [[button('⏸ Later', action='later'), button('Stop reminders', action='stop')]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def business_window(session, connection, now=None):
    now = now or datetime.now(timezone.utc)
    return bool(connection and connection['enabled'] and connection['can_reply']
        and session and session.get('business_connection_id') == connection['connection_id']
        and session.get('last_business_at') and timedelta(0) <= now - session['last_business_at'] < timedelta(hours=24))


def consent_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [button(copy.AGREE, action='consent')],
        [button(copy.BUTTONS_ONLY, action='local')],
        [button('Talk to a person', action='human'), button('Stop reminders', action='stop')]])


async def ai_answer(bot, tg_id, user, value):
    claimed = await store.claim_ai(tg_id)
    reason = reason_for(user) or 'ready'
    language = user.get('language', 'en')
    if not claimed:
        await send_business(bot, tg_id, copy.text(copy.FALLBACK, language), keyboard(reason))
        return
    history = claimed['history']
    if isinstance(history, str):
        history = json.loads(history)
    result = await ai.reply(user, value, history, consented=True)
    latest = await store.session(tg_id)
    if not latest or latest['revision'] != claimed['revision'] or latest['stopped'] or latest['handoff'] or not latest['consent']:
        return
    if not result:
        await send_business(bot, tg_id, copy.text(copy.FALLBACK, language), keyboard(reason))
        return
    action = result['action']
    if action in ('stop', 'later', 'support'):
        await store.pause(tg_id, stopped=action == 'stop', handoff=action == 'support')
        if action == 'stop':
            await db.set_notifications(tg_id, False)
        suffix = {'stop': copy.STOPPED, 'later': copy.PAUSED, 'support': copy.HUMAN}[action]
        await send_business(bot, tg_id, escape(result['reply']) + '\n\n' + copy.text(suffix, language))
        return
    nonce = secrets.token_hex(4)
    history += [{'role': 'user', 'content': ai.redact(value)},
                {'role': 'assistant', 'content': ai.redact(result['reply'])}]
    if not await store.remember(tg_id, claimed['revision'], history, result['reply'], nonce):
        return
    markup = keyboard(reason)
    targets = {'setup': 'account', 'uid': 'uid', 'recheck': 'recheck', 'signals': 'signals'}
    markup.inline_keyboard.insert(0, [button('Continue Setup' if action == 'setup' else
        {'uid': '📍 Where Is My Trader ID?', 'recheck': '🔄 Check Again', 'signals': '📊 Get Signals'}[action],
        action=targets[action])])
    markup.inline_keyboard.insert(1, [button(copy.LISTEN, action='voice:' + nonce)])
    await send_business(bot, tg_id, escape(result['reply']), markup)


async def voice_reply(bot, tg_id, nonce, language):
    claimed = await store.claim_voice(tg_id, nonce)
    if not claimed:
        return
    audio = await ai.speech(claimed['reply_text'], consented=True)
    latest = await store.session(tg_id)
    connection = await active_connection(bot)
    if not latest or latest['stopped'] or latest['handoff'] or not latest['consent'] or latest['reply_nonce'] != nonce:
        return
    if not business_window(latest, connection):
        return
    if audio is None:
        await send_business(bot, tg_id, copy.text(copy.VOICE_UNAVAILABLE, language))
        return
    await bot.send_voice(tg_id, BufferedInputFile(audio, filename='elena.mp3'),
        caption=copy.text(copy.AI_VOICE, language), business_connection_id=connection['connection_id'])


async def active_connection(bot):
    saved = await store.connection()
    if not enabled() or not saved or not saved['enabled'] or not saved['can_reply']:
        return None
    try:
        current = await bot.get_business_connection(saved['connection_id'])
    except Exception:
        # Unknown permission status fails closed, never falls back to an account session.
        return None
    if current.user.id != saved['owner_id']:
        return None
    await store.save_connection(current)
    return await store.connection() if current.is_enabled and current.rights and current.rights.can_reply else None


async def refresh(tg_id):
    """One real check, reused access rules, no Telegram messages or invented status."""
    user = await db.get_user(tg_id)
    if not user or user.get('verified') or not user.get('uid') or _check is None or _busy(tg_id) or checking(tg_id):
        return user
    checked_at = user.get('access_check_at')
    if checked_at and datetime.now(timezone.utc) - checked_at < timedelta(seconds=max(20, config.UID_LOOKUP_COOLDOWN)):
        return user
    uid = user['uid']
    _checking.add(tg_id)
    try:
        async with asyncio.timeout(config.UID_VERIFY_TIMEOUT):
            status, detail = await _check(tg_id, uid)
    except Exception:
        status, detail = config.VERIFY_TEMPORARY, None
    except asyncio.CancelledError:
        _checking.discard(tg_id)
        raise
    try:
        latest = await db.get_user(tg_id)
        if not latest or latest.get('uid') != uid or latest.get('verified') or _busy(tg_id):
            return latest
        if status == config.VERIFY_GRANTED:
            await db.set_verified(tg_id, detail)
            await db.record_access_check(tg_id, status, detail)
            await db.set_setup_stage(tg_id, 'active')
            await store.observe(tg_id, None, 0)
        else:
            await db.record_access_check(tg_id, status,
                detail if status == config.VERIFY_NEED_DEPOSIT else None)
            await db.set_setup_stage(tg_id, 'deposit' if status == config.VERIFY_NEED_DEPOSIT else 'help')
        return await db.get_user(tg_id)
    finally:
        _checking.discard(tg_id)


async def send_business(bot, tg_id, text, markup=None):
    connection = await active_connection(bot)
    session = await store.session(tg_id)
    if not business_window(session, connection):
        return False
    await bot.send_message(tg_id, text, parse_mode='HTML', reply_markup=markup,
                           business_connection_id=connection['connection_id'])
    return True


async def connection_changed(event):
    saved = await store.connection()
    if saved:
        if event.user.id != saved['owner_id']:
            return
    elif (event.user.username or '').lower() != USERNAME:
        return
    await store.save_connection(event)
    logging.info('Elena connection updated; enabled=%s; can_reply=%s',
                 event.is_enabled, bool(event.rights and event.rights.can_reply))


async def business_message(message, bot):
    if not enabled() or message.chat.type != 'private' or not message.from_user:
        return
    if message.sender_business_bot or message.via_bot or message.is_from_offline:
        return
    connection = await store.connection()
    if not connection:
        # Recover a connection update missed while the worker was being deployed.
        try:
            await connection_changed(await bot.get_business_connection(message.business_connection_id))
            connection = await store.connection()
        except Exception:
            return
    if not connection or message.business_connection_id != connection['connection_id']:
        return
    if message.from_user.id == connection['owner_id']:
        # A real operator has taken over this particular customer conversation.
        await store.pause(message.chat.id, handoff=True)
        return
    if message.from_user.id != message.chat.id:
        return
    tg_id = message.from_user.id
    value = (message.text or '').strip()
    # Interrupt pending model work immediately when the customer stops or requests a person.
    if value.lower() in ('/stop', '/unsubscribe', 'stop', 'human', '/human', 'support', 'person'):
        user = await db.get_user(tg_id)
        if not user:
            return
        stop = value.lower() in ('/stop', '/unsubscribe', 'stop')
        await store.pause(tg_id, stopped=stop, handoff=not stop)
        if stop:
            await db.set_notifications(tg_id, False)
        await send_business(bot, tg_id, copy.text(copy.STOPPED if stop else copy.HUMAN, user.get('language', 'en')))
        return
    lock = _locks.setdefault(tg_id, asyncio.Lock())
    async with lock:
        user = await db.get_user(tg_id)
        if not user:
            # Do not import unrelated private contacts into the marketing database.
            return
        if not await store.session(tg_id):
            reason = reason_for(user) or 'ready'
            await store.observe(tg_id, reason, wait_seconds(reason))
        session = await store.incoming(tg_id, connection['connection_id'], message.message_id)
        if not session:
            return
        if session['stopped'] or session['handoff']:
            return
        if not session.get('consent') and not session.get('local_only') and ai.configured():
            await send_business(bot, tg_id, copy.text(copy.CONSENT, user.get('language', 'en')), consent_keyboard())
            return
        reason = reason_for(user) or 'ready'
        if reason == 'deposit':
            user = await refresh(tg_id)
            reason = reason_for(user) or 'ready'
        if value and session.get('consent') and ai.configured():
            await ai_answer(bot, tg_id, user, value)
        else:
            await send_business(bot, tg_id, copy.prompt(user, reason), keyboard(reason))


async def callback(cb, bot):
    if not cb.message or cb.message.chat.type != 'private' or cb.from_user.id != cb.message.chat.id:
        await cb.answer()
        return
    tg_id = cb.from_user.id
    action = (cb.data or '').split(':', 1)[-1]
    # Normal-bot invitation buttons may only stop/pause. Other help is in Elena.
    business_id = getattr(cb.message, 'business_connection_id', None)
    if not business_id and action not in ('later', 'stop'):
        await cb.answer()
        return
    if business_id:
        connection = await active_connection(bot)
        if not connection or business_id != connection['connection_id']:
            await cb.answer()
            return
        if action not in ('stop', 'human', 'later') and not business_window(await store.session(tg_id), connection):
            language = await localization.language_for(tg_id)
            await cb.answer(copy.text(copy.REOPEN, language), show_alert=True)
            return
    await cb.answer()
    user = await db.get_user(tg_id)
    if not user:
        return
    language = user.get('language', 'en')
    if action in ('stop', 'human', 'later'):
        await store.pause(tg_id, stopped=action == 'stop', handoff=action == 'human')
        if action == 'stop':
            await db.set_notifications(tg_id, False)
        text = copy.text({'stop': copy.STOPPED, 'human': copy.HUMAN, 'later': copy.PAUSED}[action], language)
        if business_id:
            await send_business(bot, tg_id, text)
        else:
            await bot.send_message(tg_id, text)
        return
    lock = _locks.setdefault(tg_id, asyncio.Lock())
    async with lock:
        session = await store.session(tg_id)
        if not session:
            return
        language = user.get('language', 'en')
        if session['stopped'] or session['handoff']:
            return
        if action.startswith('voice:'):
            await voice_reply(bot, tg_id, action.split(':', 1)[1], language)
            return
        await store.pause(tg_id)
        if action == 'consent':
            await store.consent(tg_id)
            text, markup = copy.prompt(user, reason_for(user) or 'ready'), keyboard(reason_for(user) or 'ready')
        elif action == 'local':
            await store.local_only(tg_id)
            text, markup = copy.prompt(user, reason_for(user) or 'ready'), keyboard(reason_for(user) or 'ready')
        elif action == 'recheck':
            user = await refresh(tg_id)
            reason = reason_for(user) or 'ready'
            text, markup = copy.prompt(user, reason), keyboard(reason)
        else:
            sources = {'uid': copy.UID, 'account': journey.ACCOUNT_QUESTION,
                       'deposit': journey.TEXTS['payment_help'],
                       'guide': journey.TEXTS['how'], 'signals': journey.TEXTS['analysis_help']}
            if action not in sources:
                return
            text = localization.translate_message(sources[action], language)
            target = {'uid': 'uid', 'account': 'setup', 'deposit': 'setup',
                      'guide': 'guide', 'signals': 'signals'}[action]
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [button('Open Pocket IQ', url=link(target))],
                [button('Talk to a person', action='human'), button('⏸ Later', action='later')]])
        if business_id:
            await send_business(bot, tg_id, text, markup)
        else:
            await bot.send_message(tg_id, text, reply_markup=markup)


async def open_elena(message, bot):
    if message.chat.type != 'private':
        return
    connected = await active_connection(bot)
    await message.answer(copy.INVITE if connected else copy.OFFLINE,
                         reply_markup=keyboard(invitation=True))


async def status_command(message):
    if message.chat.type != 'private' or message.from_user.id not in config.ADMIN_IDS:
        return
    connection = await store.connection()
    ready = bool(connection and connection['enabled'] and connection['can_reply'])
    await message.answer('Elena account: @' + USERNAME + '\nTelegram connection: '
        + ('connected' if ready else 'not connected')
        + '\nContextual help and timers: installed'
        + '\nAI replies: ' + ('configured; customer opt-in required' if ai.configured() else 'OPENAI_API_KEY missing')
        + '\nVoice: optional Listen button; AI-generated'
        + '\nConnect Pocket IQ in Elena’s Telegram Settings > Chat Automation.'
        + '\nUsers must message Elena before she can reply from that account.'
        + '\n/remindersoff pauses scheduled reminders; /reminderson resumes them.')


class ActivityMiddleware:
    async def __call__(self, handler, event, data):
        message = getattr(event, 'message', None) or event
        user = getattr(event, 'from_user', None)
        chat = getattr(message, 'chat', None)
        normal = (enabled() and user and not user.is_bot and chat and chat.type == 'private'
                  and user.id == chat.id and not getattr(message, 'business_connection_id', None)
                  and not str(getattr(event, 'data', '')).startswith('elena:')
                  and user.id not in config.ADMIN_IDS)
        if normal:
            try:
                await store.activity(user.id)
            except Exception:
                logging.warning('Elena activity unavailable')
        failed = False
        try:
            return await handler(event, data)
        except Exception:
            failed = True
            raise
        finally:
            if normal:
                try:
                    current = await db.get_user(user.id)
                    reason = 'problem' if failed else reason_for(current)
                    state = data.get('state')
                    if (reason and state and await state.get_state() == 'Reg:waiting_uid'
                            and getattr(event, 'text', None) and not event.text.startswith('/')
                            and not re.fullmatch(r'\d{5,15}', event.text.strip())):
                        reason = 'uid'
                    await store.observe(user.id, reason, wait_seconds(reason))
                except Exception:
                    logging.warning('Elena follow-up scheduling unavailable')


async def send_job(bot, conn, job):
    tg_id = job['tg_id']
    connection = await active_connection(bot)
    if not connection or _busy(tg_id):
        return
    user = await db.get_user(tg_id)
    reason = reason_for(user)
    if reason is None:
        await store.observe(tg_id, None, 0)
        return
    if reason == 'deposit':
        user = await refresh(tg_id)
        reason = reason_for(user)
        if reason is None:
            return
    # A typing/verification update during the lookup invalidates the old job.
    if reason != job['reason'] and not (job['reason'] == 'uid' and reason == 'registration'):
        await store.observe(tg_id, reason, wait_seconds(reason))
        return
    job = await store.claim(conn, job)
    if not job:
        return
    try:
        if business_window(job, connection):
            await bot.send_message(tg_id, copy.prompt(user, reason), parse_mode='HTML',
                reply_markup=keyboard(reason), business_connection_id=connection['connection_id'])
        else:
            # A Pocket IQ invitation, never an unsolicited DM from Elena's account.
            await bot.send_message(tg_id, copy.INVITE, reply_markup=keyboard(invitation=True))
        await store.finish(conn, job, 'sent')
    except TelegramRetryAfter as exc:
        await store.finish(conn, job, 'pending' if job['attempts'] < 5 else 'failed',
                           max(1, int(exc.retry_after)) + 1)
    except TelegramForbiddenError:
        await store.finish(conn, job, 'blocked')
    except TelegramBadRequest:
        await store.finish(conn, job, 'failed')
    except asyncio.CancelledError:
        raise  # Persisted sending marker is recovered as uncertain, never resent.
    except Exception:
        await store.finish(conn, job, 'uncertain')
        logging.warning('Elena delivery uncertain; not repeating')


async def worker(bot):
    while True:
        try:
            async with db.pool.acquire() as conn:
                if not await conn.fetchval('SELECT pg_try_advisory_lock($1::bigint)', LOCK_ID):
                    await asyncio.sleep(5)
                    continue
                try:
                    await conn.execute("UPDATE elena_sessions SET status='uncertain',sent_count=sent_count+1 WHERE status='sending'")
                    logging.info('Elena assistant ready; account=@%s; awaiting authorized connection if absent', USERNAME)
                    while True:
                        await conn.execute("UPDATE elena_sessions SET history='[]'::jsonb,reply_text=NULL,reply_nonce=NULL,memory_since=NULL,revision=revision+1 WHERE memory_since<now()-interval '1 day'")
                        for job in await store.due(conn, config.ADMIN_IDS):
                            lock = _locks.setdefault(job['tg_id'], asyncio.Lock())
                            async with lock:
                                await send_job(bot, conn, job)
                            await asyncio.sleep(1.1)
                        await asyncio.sleep(30)
                finally:
                    await conn.execute('SELECT pg_advisory_unlock($1::bigint)', LOCK_ID)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Error types aid diagnosis without logging customer data or credentials.
            logging.warning('Elena queue unavailable; retrying (%s)', type(exc).__name__)
            await asyncio.sleep(5)


def install(dp, check, busy):
    global _check, _busy
    _check, _busy = check, busy
    dp.business_connection.register(connection_changed)
    dp.business_message.register(business_message)
    dp.callback_query.register(callback, F.data.startswith('elena:'))
    dp.message.register(open_elena, Command('elena'))
    dp.message.register(status_command, Command('elenastatus'))
