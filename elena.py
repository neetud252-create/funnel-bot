"""Pocket IQ -> Elena Business connection, contextual help and durable reminders.

Only @elitecsupport may attach. A different chat/account cannot select another
customer by UID. Owner-enabled AI conversation respects customer opt-outs.
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
import broadcast
import journey
import localization
import elena_copy as copy
import elena_store as store
import elena_ai as ai
import activation
from activation import USERNAME, BOT_USERNAME
from elena_timing import ReplyTiming

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


def registration_url(user):
    if not journey.safe_url(config.REF_LINK):
        return None
    sub_id = user.get('ref_code') or user.get('tg_id')
    return journey.safe_url(config.ref_url(sub_id) if sub_id else config.REF_LINK)


def registration_button(user):
    url = registration_url(user)
    return button('🔗 Registration Link', url=url) if url else button('Continue Setup', url=link('setup'))


def keyboard(reason='inactive', *, invitation=False, user=None):
    if invitation:
        rows = [[button('Chat with Elena', url=activation.chat_url())],
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
        if user and reason in ('registration', 'uid', 'problem', 'inactive'):
            rows.insert(0, [registration_button(user)])
        rows.append([button('Talk to a person', action='human')])
    rows += [[button('⏸ Later', action='later'), button('Stop reminders', action='stop')]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def business_window(session, connection, now=None):
    now = now or datetime.now(timezone.utc)
    return bool(connection and connection['enabled'] and connection['can_reply']
        and session and session.get('business_connection_id') == connection['connection_id']
        and session.get('last_business_at') and timedelta(0) <= now - session['last_business_at'] < timedelta(hours=24))


def chat_text(user, body, action='none', *, generated=False):
    language = user.get('language', 'en')
    text = escape(body) if generated else copy.text(body, language)
    def add_link(label, url):
        nonlocal text
        text += '\n\n<a href="' + escape(url, quote=True) + '">' + copy.text(label, language) + '</a>'
    if action == 'register':
        if url := registration_url(user):
            add_link('🔗 Registration Link', url)
            text += '\n' + copy.text(journey.DISCLOSURE, language)
        add_link('Open Pocket IQ', link('uid'))
    elif action != 'none':
        target = {'uid': 'uid', 'guide': 'guide', 'signals': 'signals',
                  'setup': 'setup', 'deposit': 'setup', 'recheck': 'setup'}.get(action)
        if target:
            add_link('Open Pocket IQ', link(target))
    return text


def basic_answer(user, value):
    """Local help for opted-out chats or provider failure; no model call."""
    value = (value or '').strip().casefold()
    if re.fullmatch(r'(hi|hey|hello|hii+|hola|привет|नमस्ते|مرحبا|oi|হাই|سلام|xin chào)(\s+el[ei]na)?[!?. 👋]*', value):
        return chat_text(user, copy.HELLO)
    if re.fullmatch(r'(thanks|thank you|ty|gracias|спасибо|धन्यवाद|شکریہ|obrigad[oa])[!?. ]*', value):
        return chat_text(user, copy.THANKS)
    if re.search(r'regist|sign.?up|create.{0,12}account|account.{0,12}(create|ban)|(?:bot|referral).{0,16}link|already.{0,16}account|existing.{0,8}account|रजिस|পঞ্জ|নিবন্ধ|رجس|đăng ký|daftar|kayıt|регистр|реєстр|تسجيل', value):
        existing = user.get('account_kind') == 'existing' or bool(re.search(r'already|existing|second account|duplicate', value))
        return chat_text(user, copy.CHAT_EXISTING if existing else copy.CHAT_REGISTER,
                         'uid' if existing else 'register')
    if re.search(r'\b(uid|id|trader)\b|wrong|rejected|गलत|पहचान|شناخت', value):
        return chat_text(user, copy.CHAT_UID, 'uid')
    if re.search(r'signal|analysis|trade|trading|сигнал|सिग्नल|সিগন্যাল|اشار|tín hiệu|sinyal|señal|إشار', value):
        return chat_text(user, copy.CHAT_SIGNALS, 'signals')
    if re.search(r'deposit|withdraw|payment|deposite|जमा|جمع|ডিপোজিট|nạp|çekim|плат|депозит|إيداع|سحب', value):
        return chat_text(user, copy.CHAT_PAYMENT, 'deposit')
    if re.search(r'guide|tutorial|video|गाइड|руковод|hướng dẫn|panduan|guía|دليل', value):
        return chat_text(user, copy.CHAT_SIGNALS, 'guide')
    return chat_text(user, copy.CHAT_HELP)


async def allow_ai(bot, tg_id, user):
    if not await store.consent(tg_id):
        return  # A concurrent Stop/operator takeover must not be undone by consent.
    pending = await store.take_question(tg_id)
    if pending and ai.configured():
        await ai_answer(bot, tg_id, user, pending['pending_question'])
    else:
        await send_business(bot, tg_id, copy.text(copy.HELLO, user.get('language', 'en')))


async def resume_chat(bot, tg_id, user, session):
    if session['stopped'] or session['handoff']:
        session = await store.resume(tg_id, session['revision'])
        if not session:
            return
        logging.info('Elena automatic replies resumed by customer')
    await send_business(bot, tg_id, copy.text(copy.HELLO, user.get('language', 'en')))


async def paused_reply(bot, tg_id, language):
    await send_business(bot, tg_id, copy.text(copy.CHAT_PAUSED, language))
    logging.info('Elena stale help button; a new message can continue the chat')


async def send_chat_reply(bot, tg_id, text, timing, *, revision=None, guard=True, requires_ai=False):
    """Pace ordinary conversation; recheck Stop/takeover after the wait."""
    try:
        remaining = timing.remaining()
        if remaining <= 0:
            return False
        async with asyncio.timeout(remaining):
            await timing.wait()
            if guard:
                latest = await store.session(tg_id)
                if (not latest or latest['stopped'] or latest['handoff']
                        or (revision is not None and latest['revision'] != revision)
                        or (requires_ai and latest.get('local_only'))):
                    return False
            # Includes Telegram permission checks and delivery. Never retry an
            # uncertain send, which could produce a duplicate customer message.
            return await send_business(bot, tg_id, text)
    except TimeoutError:
        logging.warning('Elena chat reply deadline reached; no duplicate retry')
        return False


async def ai_answer(bot, tg_id, user, value, *, timing=None):
    timing = timing or ReplyTiming.start()
    remaining = timing.remaining()
    if remaining <= 0:
        return
    try:
        async with asyncio.timeout(remaining):
            await _ai_answer(bot, tg_id, user, value, timing)
    except TimeoutError:
        logging.warning('Elena chat reply deadline reached; no duplicate retry')


async def _ai_answer(bot, tg_id, user, value, timing):
    claimed, result, history = None, None, []
    language = user.get('language', 'en')
    processing = timing.processing_remaining()
    if processing > 0:
        try:
            async with asyncio.timeout(processing):
                claimed = await store.claim_ai(tg_id)
                if claimed:
                    history = claimed['history']
                    if isinstance(history, str):
                        history = json.loads(history)
                    # Also bounds time waiting for the shared model semaphore.
                    result = await ai.reply(user, value, history, authorized=True)
        except TimeoutError:
            logging.info('Elena AI time budget reached; using local help')
    latest = await store.session(tg_id)
    if (not latest or (claimed and latest['revision'] != claimed['revision'])
            or latest['stopped'] or latest['handoff'] or latest.get('local_only')):
        return
    if not result:
        await send_chat_reply(bot, tg_id,
            copy.text(copy.CHAT_UNAVAILABLE, language) + '\n\n' + basic_answer(user, value),
            timing, revision=latest['revision'], requires_ai=True)
        return
    action = result['action']
    if action in ('stop', 'later', 'support'):
        await store.pause(tg_id, stopped=action == 'stop', handoff=action == 'support')
        if action == 'stop':
            await db.set_notifications(tg_id, False)
        suffix = {'stop': copy.STOPPED, 'later': copy.PAUSED, 'support': copy.HUMAN}[action]
        await send_chat_reply(bot, tg_id, escape(result['reply']) + '\n\n' + copy.text(suffix, language)
                            + ('\n\n' + copy.text(copy.CHAT_PAUSED, language) if action in ('stop', 'support') else ''),
                            timing, guard=False)
        return
    nonce = secrets.token_hex(4)
    history += [{'role': 'user', 'content': ai.private_text(user, value)},
                {'role': 'assistant', 'content': ai.private_text(user, result['reply'])}]
    if not await store.remember(tg_id, claimed['revision'], history, result['reply'], nonce):
        return
    if await send_chat_reply(bot, tg_id, chat_text(user, result['reply'], action, generated=True),
                             timing, revision=claimed['revision'], requires_ai=True):
        logging.info('Elena AI reply delivered; action=%s', action)


async def voice_reply(bot, tg_id, nonce, language):
    claimed = await store.claim_voice(tg_id, nonce)
    if not claimed:
        return
    audio = await ai.speech(claimed['reply_text'], authorized=True)
    latest = await store.session(tg_id)
    connection = await active_connection(bot)
    if not latest or latest['stopped'] or latest['handoff'] or latest.get('local_only') or latest['reply_nonce'] != nonce:
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
        logging.info('Elena reply skipped; business reply window or connection unavailable')
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
    timing = ReplyTiming.start()
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
    if (not connection or not connection['enabled'] or not connection['can_reply']
            or message.business_connection_id != connection['connection_id']):
        return
    if message.from_user.id == connection['owner_id']:
        # A real operator has taken over this particular customer conversation.
        await store.pause(message.chat.id, handoff=True)
        logging.info('Elena automatic replies paused; operator takeover')
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
        language = user.get('language', 'en')
        await send_business(bot, tg_id, copy.text(copy.STOPPED if stop else copy.HUMAN, language)
                            + '\n\n' + copy.text(copy.CHAT_PAUSED, language))
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
        if activation.enabled() and not activation.current(session):
            reason = reason_for(user) or 'ready'
            activated = await store.activate(tg_id, connection['connection_id'],
                                             message.message_id, reason, wait_seconds(reason))
            if activated:
                # Both chats update immediately; neither waits for AI or a timer.
                await asyncio.gather(confirm_activation(bot, tg_id, user),
                                     activation.deliver(bot, tg_id))
                if not value or value == activation.DRAFT:
                    return
                session = activated
        language = user.get('language', 'en')
        command = value.casefold().strip(' .!?')
        if command == '/privacy':
            await send_business(bot, tg_id, copy.text(copy.CHAT_PRIVACY, language))
            return
        if command in ('/resume', 'resume'):
            await resume_chat(bot, tg_id, user, session)
            return
        if session['stopped'] or session['handoff']:
            if not value:
                return
            # A fresh customer message reopens conversation and is itself the
            # question to answer. Do not require a command or send a placeholder.
            # A newer Stop/operator event still wins through the revision check.
            session = await store.resume(tg_id, session['revision'])
            if not session:
                return
            logging.info('Elena conversation reopened by a new customer message')
        if command == '/ai':
            if ai.configured():
                await allow_ai(bot, tg_id, user)
            else:
                await send_business(bot, tg_id, copy.text(copy.CHAT_UNAVAILABLE, language))
            return
        if command == '/basic':
            await store.local_only(tg_id)
            await send_business(bot, tg_id, copy.text(copy.CHAT_BASIC, language))
            return
        # Honor answers to permission offers sent by the previous release.
        # New conversations never send an offer or require YES/NO.
        if not session.get('consent') and session.get('consent_prompted_at'):
            if command in ('yes', 'allow ai', 'да', 'так', 'हाँ', 'हां', 'হ্যাঁ', 'جی', 'ہاں', 'có', 'ya', 'evet', 'sí', 'si', 'نعم', 'sim'):
                await allow_ai(bot, tg_id, user)
                return
            if command in ('no', 'нет', 'ні', 'नहीं', 'না', 'نہیں', 'không', 'tidak', 'hayır', 'لا', 'não'):
                await store.local_only(tg_id)
                await send_business(bot, tg_id, copy.text(copy.CHAT_BASIC, language))
                return
        if command == '/voice':
            if not session.get('local_only') and session.get('reply_nonce'):
                await voice_reply(bot, tg_id, session['reply_nonce'], language)
            else:
                await send_business(bot, tg_id, copy.text(copy.CHAT_VOICE, language))
            return
        reason = reason_for(user) or 'ready'
        if reason == 'deposit' and timing.processing_remaining() > 0:
            try:
                # A slow broker lookup must not consume the conversation budget.
                async with asyncio.timeout(min(1.5, timing.processing_remaining())):
                    user = await refresh(tg_id)
            except TimeoutError:
                logging.info('Elena quick status lookup timed out; using saved status')
        if value and not session.get('local_only') and ai.configured():
            await ai_answer(bot, tg_id, user, value, timing=timing)
        else:
            await send_chat_reply(bot, tg_id, basic_answer(user, value), timing, revision=session['revision'])


async def confirm_activation(bot, tg_id, user):
    # This response to the new activation message does not resume marketing
    # reminders, revoke human handoff, or grant OpenAI consent.
    try:
        language = user.get('language', 'en')
        await send_business(bot, tg_id, copy.text(copy.ACTIVATED, language)
                            + '\n\n' + copy.text(copy.CHAT_NOTICE, language))
    except Exception as exc:
        logging.warning('Activation confirmation unavailable (%s)', type(exc).__name__)


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
        if (action not in ('stop', 'human', 'later') and activation.enabled()
                and not activation.current(await store.session(tg_id))):
            language = await localization.language_for(tg_id)
            await cb.answer(copy.text(copy.REOPEN, language), show_alert=True)
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
            await send_business(bot, tg_id, text + ('\n\n' + copy.text(copy.CHAT_PAUSED, language)
                                                   if action in ('stop', 'human') else ''))
        else:
            await bot.send_message(tg_id, text)
        return
    lock = _locks.setdefault(tg_id, asyncio.Lock())
    async with lock:
        session = await store.session(tg_id)
        if not session:
            return
        language = user.get('language', 'en')
        if action == 'resume':
            await resume_chat(bot, tg_id, user, session)
            return
        if session['stopped'] or session['handoff']:
            await paused_reply(bot, tg_id, language)
            return
        if action.startswith('voice:'):
            await voice_reply(bot, tg_id, action.split(':', 1)[1], language)
            return
        await store.pause(tg_id)
        if action == 'consent':
            await allow_ai(bot, tg_id, user)
            return
        elif action == 'local':
            await store.local_only(tg_id)
            text = copy.text(copy.CHAT_BASIC, language)
        elif action == 'recheck':
            user = await refresh(tg_id)
            reason = reason_for(user) or 'ready'
            source = {'ready': journey.READY, 'temporary': journey.PENDING,
                      'uid': copy.CHAT_UID, 'deposit': copy.CHAT_PAYMENT}.get(reason, copy.CHAT_HELP)
            text = chat_text(user, source, 'signals' if reason == 'ready' else 'setup')
        else:
            if action == 'account' and not session.get('local_only') and ai.configured():
                await ai_answer(bot, tg_id, user, 'Help me set up my account. Explain the registration steps and how to submit my Trader ID.')
                return
            sources = {'uid': copy.CHAT_UID, 'account': copy.CHAT_REGISTER,
                       'deposit': copy.CHAT_PAYMENT,
                       'guide': copy.CHAT_SIGNALS, 'signals': copy.CHAT_SIGNALS}
            if action not in sources:
                return
            text = basic_answer(user, 'how do I register') if action == 'account' else chat_text(user, sources[action], action)
        if business_id:
            await send_business(bot, tg_id, text)
        else:
            await bot.send_message(tg_id, text)


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
        + '\nAI replies: ' + ('configured; direct replies enabled' if ai.configured() else 'OPENAI_API_KEY missing')
        + '\nVoice: optional /voice command; AI-generated'
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
                  and not str(getattr(event, 'data', '')).startswith('elena:'))
        if normal and user.id in config.ADMIN_IDS:
            # Activated owners can exercise the customer journey. Operator
            # commands, reports and broadcast drafting must not move its timer.
            text = (getattr(event, 'text', None) or '').strip()
            command = text.split(maxsplit=1)[0].split('@')[0].lower() if text else ''
            normal = (not command.startswith('/') or command == '/start') and not str(
                getattr(event, 'data', '')).startswith(('an:', 'bc:', 'vr:'))
            if normal:
                try:
                    normal = activation.current(await store.session(user.id))
                    if normal and text and not command.startswith('/'):
                        normal = not await broadcast.draft(user.id)
                except Exception:
                    normal = False
                    logging.warning('Elena admin test eligibility unavailable')
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
                    if user.id in config.ADMIN_IDS:
                        logging.info('Elena admin test follow-up updated; reason=%s; delay=%ss',
                                     reason or 'resolved', wait_seconds(reason) if reason else 0)
                except Exception:
                    logging.warning('Elena follow-up scheduling unavailable')


async def send_job(bot, conn, job):
    tg_id = job['tg_id']
    if activation.enabled() and not activation.current(job):
        # Pre-update reminders cannot present old feature controls before the
        # customer has completed the newly required activation.
        await store.await_chat(conn, job)
        return
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
    if not business_window(job, connection):
        # No automatic invitation or normal-bot fallback when Elena cannot DM.
        await store.await_chat(conn, job)
        return
    job = await store.claim(conn, job)
    if not job:
        return
    try:
        source = {'uid': copy.CHAT_UID, 'deposit': copy.CHAT_PAYMENT,
                  'registration': copy.CHAT_HELP}.get(reason, copy.CHAT_HELP)
        await bot.send_message(tg_id, chat_text(user, source, 'uid' if reason == 'uid' else 'setup'), parse_mode='HTML',
            business_connection_id=connection['connection_id'])
        await store.finish(conn, job, 'sent')
        logging.info('Elena follow-up delivered; reason=%s; admin=%s',
                     reason, tg_id in config.ADMIN_IDS)
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
                        await conn.execute("UPDATE elena_sessions SET pending_question=NULL,pending_question_at=NULL WHERE pending_question_at<now()-interval '1 day'")
                        if activation.enabled():
                            for pending in await store.pending_activations(conn):
                                await activation.deliver(bot, pending['tg_id'])
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
