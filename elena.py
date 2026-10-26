"""Pocket IQ -> Elina Business connection, contextual help and durable reminders.

Only @elitecsupport may attach. A different chat/account cannot select another
customer by UID. Owner-enabled AI conversation respects customer opt-outs.
Elina is the single support channel; links are always attached when asked.
"""
import asyncio
import logging
import os
import random
import re
import json
import secrets
from html import escape, unescape
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
import elena_messages as messages
import activation
import chart_trial
from activation import USERNAME, BOT_USERNAME
from elena_timing import ReplyTiming
from elena_mode import activation_only

LOCK_ID = 8790198820
_locks = weakref.WeakValueDictionary()
_check = None
_busy = lambda uid: False
_checking = set()
_touch = {}   # tg_id -> (reason, touch_count); escalates the follow-up chain

# Human-like pause between bubbles (seconds).
BUBBLE_MIN = float(os.getenv('ELENA_BUBBLE_MIN', '3'))
BUBBLE_MAX = float(os.getenv('ELENA_BUBBLE_MAX', '5'))

# If the user asks for the link, always attach it even if the model said "none".
LINK_REQUEST = re.compile(r'(?i)\b(link|register|registration|sign.?up|referral|join|refer)\b')


def checking(tg_id):
    return tg_id in _checking


def enabled():
    return os.getenv('ELENA_ENABLED', '1').lower() in ('1', 'true', 'yes', 'on')


def reason_for(user):
    if not user or user.get('verified'):
        return None
    status = user.get('access_check_status')
    if status == config.VERIFY_NEED_DEPOSIT and user.get('access_check_deposit') is not None:
        if user['access_check_deposit'] >= config.MIN_DEPOSIT:
            return None
        return 'deposit'
    if status in (config.VERIFY_WRONG_LINK, config.VERIFY_INVALID_UID):
        return 'uid'
    # A failed/pending check is not evidence that an account or deposit is wrong.
    return 'inactive' if not user.get('uid') and not status else None


def wait_seconds(reason):
    chain = messages.FOLLOWUP_DELAYS.get(reason)
    return chain[0] if chain else 0


def next_delay(reason, touch):
    chain = messages.FOLLOWUP_DELAYS.get(reason)
    return chain[touch + 1] if chain and touch + 1 < len(chain) else 0


def touch_index(tg_id, reason):
    stored = _touch.get(tg_id)
    if not stored or stored[0] != reason:
        _touch[tg_id] = (reason, 0)
        return 0
    return stored[1]


def bump_touch(tg_id, reason):
    stored = _touch.get(tg_id)
    count = (stored[1] if stored and stored[0] == reason else 0) + 1
    _touch[tg_id] = (reason, count)


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
        rows = [[button('Chat with Elina', url=activation.chat_url())],
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
        # Only the registration link — no commission/disclosure line.
        if url := registration_url(user):
            add_link(copy.LINK_REGISTER, url)
        else:
            add_link(copy.LINK_OPEN, link('setup'))
    elif action != 'none':
        target = {'uid': 'uid', 'guide': 'guide', 'signals': 'signals',
                  'setup': 'setup', 'deposit': 'setup', 'recheck': 'setup'}.get(action)
        if target:
            add_link(copy.LINK_OPEN, link(target))
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
    if re.search(r'accura|win.?rate|percent|%|success|hit.?rate|सटीक|정확|دقت|doğruluk|akurasi|độ chính xác', value):
        return chat_text(user, copy.CHAT_ACCURACY + '\n\n' + copy.CHAT_ACCURACY_2)
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
        logging.info('Elina automatic replies resumed by customer')
    await send_business(bot, tg_id, copy.text(copy.HELLO, user.get('language', 'en')))


async def paused_reply(bot, tg_id, language):
    await send_business(bot, tg_id, copy.text(copy.CHAT_PAUSED, language))
    logging.info('Elina stale help button; a new message can continue the chat')


async def send_chat_reply(bot, tg_id, text, timing, *, revision=None, guard=True, requires_ai=False):
    """Pace ordinary conversation; recheck Stop/takeover after each gap."""
    try:
        remaining = timing.remaining()
        if remaining <= 0:
            return False
        async with asyncio.timeout(remaining):
            await timing.wait()
            for index, part in enumerate(messages.bubbles(text)):
                if index:
                    # Real-person gap between messages.
                    await asyncio.sleep(random.uniform(BUBBLE_MIN, BUBBLE_MAX))
                if guard:
                    latest = await store.session(tg_id)
                    if (not latest or latest['stopped'] or latest['handoff']
                            or (revision is not None and latest['revision'] != revision)
                            or (requires_ai and latest.get('local_only'))):
                        return False
                if not await send_business(bot, tg_id, part):
                    return False
            return True
    except TimeoutError:
        logging.warning('Elina chat reply deadline reached; no duplicate retry')
        return False


async def ai_answer(bot, tg_id, user, value, *, timing=None):
    if activation_only():
        return
    timing = timing or ReplyTiming.start()
    remaining = timing.remaining()
    if remaining <= 0:
        return
    try:
        async with asyncio.timeout(remaining):
            await _ai_answer(bot, tg_id, user, value, timing)
    except TimeoutError:
        logging.warning('Elina chat reply deadline reached; no duplicate retry')


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
                    result = await ai.reply(user, value, history, authorized=True)
        except TimeoutError:
            logging.info('Elina AI time budget reached; using local help')
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
    # Hard guarantee: attach the registration link whenever one is asked for,
    # or whenever the model talks about a link but chose action "none".
    if action == 'none' and (LINK_REQUEST.search(value or '')
                             or re.search(r'(?i)\blink\b', result['reply'])):
        action = 'register'
    if action in ('stop', 'later', 'support'):
        await store.pause(tg_id, stopped=action == 'stop', handoff=action == 'support')
        if action == 'stop':
            await db.set_notifications(tg_id, False)
        body = escape(result['reply'])
        if action == 'stop':
            body += '\n\n' + copy.text(copy.STOPPED, language)
        elif action == 'later':
            body += '\n\n' + copy.text(copy.PAUSED, language)
        # 'support': nothing appended — no "paused / team will help" notice.
        await send_chat_reply(bot, tg_id, body, timing, guard=False)
        return
    nonce = secrets.token_hex(4)
    history += [{'role': 'user', 'content': ai.private_text(user, value)},
                {'role': 'assistant', 'content': ai.private_text(user, result['reply'])}]
    if not await store.remember(tg_id, claimed['revision'], history, result['reply'], nonce):
        return
    if await send_chat_reply(bot, tg_id, chat_text(user, result['reply'], action, generated=True),
                             timing, revision=claimed['revision'], requires_ai=True):
        logging.info('Elina AI reply delivered; action=%s', action)


async def voice_reply(bot, tg_id, nonce, language):
    if activation_only():
        return
    claimed = await store.claim_voice(tg_id, nonce)
    if not claimed:
        return
    audio = await ai.speech(claimed['reply_text'], authorized=True)
    latest = await store.session(tg_id)
    connection = await active_connection(bot)
    if not latest or latest['stopped'] or latest['handoff'] or latest.get('local_only') or latest['reply_nonce'] != nonce:
        return
    if activation_only() or not business_window(latest, connection):
        return
    if audio is None:
        await send_business(bot, tg_id, copy.text(copy.VOICE_UNAVAILABLE, language))
        return
    await bot.send_voice(tg_id, BufferedInputFile(audio, filename='elina.mp3'),
        caption=copy.text(copy.AI_VOICE, language), business_connection_id=connection['connection_id'])


async def active_connection(bot):
    saved = await store.connection()
    if not enabled() or not saved or not saved['enabled'] or not saved['can_reply']:
        return None
    try:
        current = await bot.get_business_connection(saved['connection_id'])
    except Exception:
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


async def send_business(bot, tg_id, text, markup=None, *, activation_confirmation=False):
    if activation_only() and not activation_confirmation:
        return False
    connection = await active_connection(bot)
    session = await store.session(tg_id)
    if not business_window(session, connection):
        logging.info('Elina reply skipped; business reply window or connection unavailable')
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
    logging.info('Elina connection updated; enabled=%s; can_reply=%s',
                 event.is_enabled, bool(event.rights and event.rights.can_reply))


async def business_message(message, bot):
    timing = ReplyTiming.start()
    if not enabled() or message.chat.type != 'private' or not message.from_user:
        return
    if message.sender_business_bot or message.via_bot or message.is_from_offline:
        return
    connection = await store.connection()
    if not connection:
        try:
            await connection_changed(await bot.get_business_connection(message.business_connection_id))
            connection = await store.connection()
        except Exception:
            return
    if (not connection or not connection['enabled'] or not connection['can_reply']
            or message.business_connection_id != connection['connection_id']):
        return
    if message.from_user.id == connection['owner_id']:
        await store.pause(message.chat.id, handoff=True)
        logging.info('Elina automatic replies paused; operator takeover')
        return
    if message.from_user.id != message.chat.id:
        return
    tg_id = message.from_user.id
    value = (message.text or '').strip()
    if value.lower() in ('/stop', '/unsubscribe', 'stop', 'human', '/human', 'support', 'person'):
        user = await db.get_user(tg_id)
        if not user:
            return
        stop = value.lower() in ('/stop', '/unsubscribe', 'stop')
        await store.pause(tg_id, stopped=stop, handoff=not stop)
        if stop:
            await db.set_notifications(tg_id, False)
        if activation_only():
            return
        if stop:
            await send_business(bot, tg_id, copy.text(copy.STOPPED, user.get('language', 'en')))
        # human/support: no notice
        return
    lock = _locks.setdefault(tg_id, asyncio.Lock())
    async with lock:
        user = await db.get_user(tg_id)
        if not user:
            return
        # A genuine customer message restarts the escalation chain from touch 1.
        _touch.pop(tg_id, None)
        if not await store.session(tg_id):
            reason = reason_for(user) or 'ready'
            await store.observe(tg_id, reason, wait_seconds(reason))
        session = await store.incoming(tg_id, connection['connection_id'], message.message_id)
        if not session:
            return
        if activation.enabled() and not activation.current(session):
            reason = 'ready' if activation_only() else reason_for(user) or 'ready'
            activated = await store.activate(tg_id, connection['connection_id'],
                                             message.message_id, reason, wait_seconds(reason))
            if activated:
                await asyncio.gather(confirm_activation(bot, tg_id, user),
                                     activation.deliver(bot, tg_id))
                if not value or value == activation.DRAFT:
                    return
                session = activated
        if activation_only():
            return
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
            session = await store.resume(tg_id, session['revision'])
            if not session:
                return
            logging.info('Elina conversation reopened by a new customer message')
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
                async with asyncio.timeout(min(1.5, timing.processing_remaining())):
                    user = await refresh(tg_id)
            except TimeoutError:
                logging.info('Elina quick status lookup timed out; using saved status')
        if value and not session.get('local_only') and ai.configured():
            await ai_answer(bot, tg_id, user, value, timing=timing)
        else:
            await send_chat_reply(bot, tg_id, basic_answer(user, value), timing, revision=session['revision'])


async def confirm_activation(bot, tg_id, user):
    try:
        language = user.get('language', 'en')
        text = copy.text(copy.ACTIVATED, language)
        if not activation_only():
            note = copy.text(copy.CHAT_NOTICE, language)
            if note:
                text += '\n\n' + note
        await send_business(bot, tg_id, text, activation_confirmation=True)
    except Exception as exc:
        logging.warning('Activation confirmation unavailable (%s)', type(exc).__name__)


async def callback(cb, bot):
    if not cb.message or cb.message.chat.type != 'private' or cb.from_user.id != cb.message.chat.id:
        await cb.answer()
        return
    tg_id = cb.from_user.id
    action = (cb.data or '').split(':', 1)[-1]
    business_id = getattr(cb.message, 'business_connection_id', None)
    if not business_id and action not in ('later', 'stop'):
        await cb.answer()
        return
    if business_id:
        connection = await active_connection(bot)
        if not connection or business_id != connection['connection_id']:
            await cb.answer()
            return
        if activation_only() and action not in ('stop', 'human', 'later'):
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
        if activation_only() or action == 'human':
            return  # human: no notice
        text = copy.text(copy.STOPPED if action == 'stop' else copy.PAUSED, language)
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
    if activation_only():
        await message.answer('Elina account: @' + USERNAME + '\nTelegram connection: '
            + ('connected' if ready else 'not connected')
            + '\nMode: activation only'
            + '\nActivation confirmation and Pocket IQ menu handoff: enabled'
            + '\nChat replies, AI, voice and scheduled reminders: disabled')
        return
    await message.answer('Elina account: @' + USERNAME + '\nTelegram connection: '
        + ('connected' if ready else 'not connected')
        + '\nFollow-up chain: no UID 10m/30m/3h/24h/48h; wrong UID 5m/1h/6h/24h/48h; deposit 15m/1h/6h/24h/72h'
        + '\nAI replies: ' + ('configured; direct replies enabled' if ai.configured() else 'OPENAI_API_KEY missing')
        + '\nVoice: optional /voice command; AI-generated'
        + '\nConnect Pocket IQ in Elina’s Telegram Settings > Chat Automation.'
        + '\nUsers must message Elina before she can reply from that account.'
        + '\n/remindersoff pauses scheduled reminders; /reminderson resumes them.')


class ActivityMiddleware:
    async def __call__(self, handler, event, data):
        message = getattr(event, 'message', None) or event
        user = getattr(event, 'from_user', None)
        chat = getattr(message, 'chat', None)
        normal = (enabled() and not activation_only() and user and not user.is_bot and chat and chat.type == 'private'
                  and user.id == chat.id and not getattr(message, 'business_connection_id', None)
                  and not str(getattr(event, 'data', '')).startswith('elena:'))
        if normal and user.id in config.ADMIN_IDS:
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
                    logging.warning('Elina admin test eligibility unavailable')
        if normal:
            try:
                await store.activity(user.id)
            except Exception:
                logging.warning('Elina activity unavailable')
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
                    reason = None if failed else reason_for(current)
                    state = data.get('state')
                    if (reason and state and await state.get_state() == 'Reg:waiting_uid'
                            and getattr(event, 'text', None) and not event.text.startswith('/')
                            and not re.fullmatch(r'\d{5,15}', event.text.strip())):
                        reason = 'uid'
                    _touch.pop(user.id, None)
                    await store.observe(user.id, reason, wait_seconds(reason))
                    if user.id in config.ADMIN_IDS:
                        logging.info('Elina admin test follow-up updated; reason=%s; delay=%ss',
                                     reason or 'resolved', wait_seconds(reason) if reason else 0)
                except Exception:
                    logging.warning('Elina follow-up scheduling unavailable')


async def send_job(bot, conn, job):
    if activation_only():
        return
    tg_id = job['tg_id']
    if activation.enabled() and not activation.current(job):
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
    if reason != job['reason']:
        await store.observe(tg_id, reason, wait_seconds(reason))
        return
    if not business_window(job, connection):
        await store.await_chat(conn, job)
        return
    if reason == 'inactive':
        _, remaining = await chart_trial._status(conn, tg_id)
        user = dict(user, free_analyses_remaining=remaining)
    touch = touch_index(tg_id, reason)
    job = await store.claim(conn, job)
    if not job:
        return
    delivered = 0
    sent_parts = []
    try:
        for index, part in enumerate(messages.followup(user, reason, registration_url(user),
                                      link('uid' if reason == 'uid' else 'setup'), touch=touch)):
            if index:
                await asyncio.sleep(random.uniform(BUBBLE_MIN, BUBBLE_MAX))
            latest, current = await store.session(tg_id), await db.get_user(tg_id)
            if (not latest or latest['revision'] != job['revision'] or latest['stopped'] or latest['handoff']
                    or not current or current.get('broadcast_opt_out') or current.get('broadcast_blocked')
                    or reason_for(current) != reason):
                await store.finish(conn, job, 'cancelled')
                return
            await bot.send_message(tg_id, part, parse_mode='HTML',
                business_connection_id=connection['connection_id'])
            delivered += 1
            sent_parts.append(part)
        await store.finish(conn, job, 'sent')
        # Advance the escalation chain; stop when the last touch is delivered.
        bump_touch(tg_id, reason)
        if (delay := next_delay(reason, touch)):
            await store.observe(tg_id, reason, delay)
        try:
            plain = unescape(re.sub(r'<[^>]+>', '', '\n'.join(sent_parts)))
            await store.remember_followup(tg_id, job['revision'], ai.private_text(user, plain))
        except Exception:
            logging.warning('Elina follow-up memory unavailable; delivery already recorded')
        logging.info('Elina follow-up delivered; reason=%s; touch=%s; admin=%s',
                     reason, touch, tg_id in config.ADMIN_IDS)
    except TelegramRetryAfter as exc:
        retry = not delivered and job['attempts'] < 5
        await store.finish(conn, job, 'pending' if retry else 'partial' if delivered else 'failed',
                           max(1, int(exc.retry_after)) + 1 if retry else None)
    except TelegramForbiddenError:
        await store.finish(conn, job, 'blocked')
    except TelegramBadRequest:
        await store.finish(conn, job, 'failed')
    except asyncio.CancelledError:
        raise
    except Exception:
        await store.finish(conn, job, 'uncertain')
        logging.warning('Elina delivery uncertain; not repeating')


async def worker(bot):
    if not activation_only() and os.getenv('ELENA_STARTUP_CHECK') == '1':
        try:
            connection = await active_connection(bot)
            async with db.pool.acquire() as probe:
                reminders = await probe.fetchval('SELECT enabled FROM verification_reminder_settings WHERE id=1')
            logging.info('Elina startup check: business_connected=%s; registration_link=%s; minimum_deposit=%s',
                         bool(connection), bool(registration_url({})), config.MIN_DEPOSIT)
            logging.info('Elina startup check: scheduled_reminders=%s', reminders)
            result = await ai.reply({'language': 'en', 'setup_stage': 'new'}, 'Hi', [], authorized=True)
            logging.info('Elina startup check: openai_reply=%s; model=%s', bool(result), ai.MODEL)
        except Exception as exc:
            logging.warning('Elina startup check unavailable (%s)', type(exc).__name__)
    while True:
        try:
            async with db.pool.acquire() as conn:
                if not await conn.fetchval('SELECT pg_try_advisory_lock($1::bigint)', LOCK_ID):
                    await asyncio.sleep(5)
                    continue
                try:
                    await conn.execute("UPDATE elena_sessions SET status='uncertain',sent_count=sent_count+1 WHERE status='sending'")
                    logging.info('Elina assistant ready; account=@%s; activation_only=%s', USERNAME, activation_only())
                    while True:
                        await conn.execute("UPDATE elena_sessions SET history='[]'::jsonb,reply_text=NULL,reply_nonce=NULL,memory_since=NULL,revision=revision+1 WHERE memory_since<now()-interval '1 day'")
                        await conn.execute("UPDATE elena_sessions SET pending_question=NULL,pending_question_at=NULL WHERE pending_question_at<now()-interval '1 day'")
                        if activation.enabled():
                            for pending in await store.pending_activations(conn):
                                await activation.deliver(bot, pending['tg_id'])
                        jobs = [] if activation_only() else await store.due(conn, config.ADMIN_IDS)
                        for job in jobs:
                            lock = _locks.setdefault(job['tg_id'], asyncio.Lock())
                            async with lock:
                                await send_job(bot, conn, job)
                            await asyncio.sleep(1.1)
                        await asyncio.sleep(5)
                finally:
                    await conn.execute('SELECT pg_advisory_unlock($1::bigint)', LOCK_ID)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logging.warning('Elina queue unavailable; retrying (%s)', type(exc).__name__)
            await asyncio.sleep(5)


def install(dp, check, busy):
    global _check, _busy
    _check, _busy = check, busy
    dp.business_connection.register(connection_changed)
    dp.business_message.register(business_message)
    dp.callback_query.register(callback, F.data.startswith('elena:'))
    dp.message.register(open_elena, Command('elena'))
    dp.message.register(status_command, Command('status'))
    # Remove this line if your original registered ActivityMiddleware elsewhere.
    dp.update.outer_middleware(ActivityMiddleware())
    return worker
