"""Private funnel telemetry. No message bodies, screenshots or account IDs are stored."""
import asyncio
from contextvars import ContextVar
import hashlib
import json
import logging
import re
import secrets
from string import Formatter
from urllib.parse import urlsplit

from aiogram import BaseMiddleware
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response
import config
import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS analytics_events (
 id BIGSERIAL PRIMARY KEY, tg_id BIGINT NOT NULL, kind TEXT NOT NULL,
 action TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '',
 screen TEXT NOT NULL DEFAULT '', message_id BIGINT,
 dedupe_key TEXT UNIQUE, created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS analytics_events_time ON analytics_events(created_at,kind);
CREATE INDEX IF NOT EXISTS analytics_events_user ON analytics_events(tg_id,created_at DESC,id DESC);
CREATE TABLE IF NOT EXISTS analytics_messages (
 tg_id BIGINT NOT NULL, message_id BIGINT NOT NULL, screen TEXT NOT NULL,
 buttons JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tg_id,message_id)
);
CREATE INDEX IF NOT EXISTS analytics_messages_time ON analytics_messages(created_at);
CREATE TABLE IF NOT EXISTS analytics_links (
 token TEXT PRIMARY KEY, tg_id BIGINT NOT NULL, screen TEXT NOT NULL,
 action TEXT NOT NULL, label TEXT NOT NULL, destination TEXT NOT NULL,
 message_id BIGINT, created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
INSERT INTO bot_settings(setting_key,setting_value)
 VALUES('detailed_analytics_started_at',now()::text) ON CONFLICT DO NOTHING;
"""
_screen = ContextVar('analytics_screen', default=None)
_patterns = None
router = APIRouter()


def enabled(uid):
    return (isinstance(uid, int) and uid > 0 and uid not in config.ADMIN_IDS
            and getattr(db, 'pool', None) is not None)


async def record(uid, kind, action='', *, label='', screen='', message_id=None, dedupe_key=None):
    if not enabled(uid):
        return
    try:
        async with asyncio.timeout(2):
            async with db.pool.acquire() as conn:
                await conn.execute("""INSERT INTO analytics_events
                    (tg_id,kind,action,label,screen,message_id,dedupe_key)
                    VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT(dedupe_key) DO NOTHING""",
                    uid, kind, action[:100], label[:120], screen[:80], message_id, dedupe_key)
    except Exception:
        logging.exception('Detailed analytics write failed: %s', kind)


def _template(text):
    return re.compile(''.join(re.escape(literal) + ('.*?' if field else '')
                            for literal, field, _, _ in Formatter().parse(text)), re.S)


def screen_name(text):
    """Identify built-in screens before localization, without persisting their text."""
    global _patterns
    if _patterns is None:
        import home_menu
        import localization
        pairs = [('home', home_menu.TEXT), ('language', localization.SELECTOR_TEXT),
                 ('money_management', home_menu.SHEET_CAPTION)]
        pairs += [(key, value['text']) for key, value in config.SCREENS.items()]
        pairs += [(name.removeprefix('MSG_').lower(), value)
                  for name, value in vars(config).items()
                  if name.startswith('MSG_') and not name.startswith(('MSG_ADMIN_', 'MSG_TOKENS_'))
                  and isinstance(value, str)]
        pairs += [('signal_result', config.SIGNAL_RESULT), ('signal_wait', config.SIGNAL_ANALYZING)]
        _patterns = [(key, _template(value)) for key, value in pairs]
    for key, pattern in _patterns:
        if pattern.fullmatch(text or ''):
            return key
    return 'other'


def button_info(button):
    if button.callback_data:
        # Broadcast IDs change, but their public action is the same button.
        action = 'bc:open' if button.callback_data.startswith('bc:open:') else button.callback_data
    elif button.url:
        # Group a per-user affiliate URL by its label, not its query parameters.
        action = 'url:' + hashlib.sha256(button.text.encode()).hexdigest()[:16]
    else:
        return None
    return {'action': action, 'label': button.text[:120], 'callback': button.callback_data,
            'type': 'link' if button.url else 'callback'}


class InboundMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if getattr(getattr(event, 'message', None), 'business_connection_id', None):
            return await handler(event, data)
        user = getattr(event, 'from_user', None)
        message = getattr(event, 'message', None)
        chat = getattr(event, 'chat', None) or getattr(message, 'chat', None)
        track = bool(user and not user.is_bot and chat and chat.type == 'private' and enabled(user.id))
        if track:
            try:
                async with asyncio.timeout(2):
                    await db.touch_user(user.id, user.username)
                    if message is not None and isinstance(getattr(event, 'data', None), str):
                        async with db.pool.acquire() as conn:
                            row = await conn.fetchrow('SELECT screen,buttons FROM analytics_messages '
                                                      'WHERE tg_id=$1 AND message_id=$2',
                                                      user.id, message.message_id)
                        buttons = json.loads(row['buttons']) if row and isinstance(row['buttons'], str) else (
                            row['buttons'] if row else [])
                        selected = next((b for b in buttons if b.get('callback') == event.data), None)
                        if not selected:
                            # Older messages still work. Count the tap, but do not guess a screen.
                            markup = getattr(message, 'reply_markup', None)
                            selected = next((button_info(b) for r in getattr(markup, 'inline_keyboard', [])
                                             for b in r if b.callback_data == event.data), None)
                        selected = selected or {'action': 'unknown', 'label': 'Unknown or expired button'}
                        await record(user.id, 'button_click', selected['action'], label=selected['label'],
                                     screen=row['screen'] if row else 'legacy', message_id=message.message_id,
                                     dedupe_key='callback:' + str(event.id))
                    else:
                        text = getattr(event, 'text', None) or ''
                        command = text.split(maxsplit=1)[0].split('@', 1)[0] if text.startswith('/') else ''
                        kind = 'start' if command == '/start' else 'message'
                        # Store only the content category, never free text or command arguments.
                        category = 'command' if command else ('photo' if getattr(event, 'photo', None)
                                   else 'document' if getattr(event, 'document', None) else 'text_or_other')
                        await record(user.id, kind, category, message_id=event.message_id,
                                     dedupe_key=f'message:{user.id}:{event.message_id}')
            except Exception:
                logging.exception('Inbound analytics failed; continuing user action')
        try:
            return await handler(event, data)
        except Exception:
            if track:
                await record(user.id, 'handler_error', 'interaction_failed')
            raise


class OutboundMiddleware:
    """Observe successful deliveries and wrap URL buttons before localization."""
    async def __call__(self, make_request, bot, method):
        # Business message IDs belong to a different chat namespace. Never track
        # their private conversations or collide with the Pocket IQ UI records.
        if getattr(method, 'business_connection_id', None):
            return await make_request(bot, method)
        uid = getattr(method, 'chat_id', None)
        api = method.__api_method__
        if not enabled(uid) or api not in ('sendMessage', 'sendPhoto', 'sendVideo', 'sendAnimation', 'sendDocument'):
            return await make_request(bot, method)
        text = getattr(method, 'text', None) or getattr(method, 'caption', None) or ''
        markup = getattr(method, 'reply_markup', None)
        screen = screen_name(text)
        if screen == 'other':
            screen = _screen.get() or screen
        if screen == 'other' and not markup:
            return await make_request(bot, method)
        buttons, tokens = [], []
        updated = method
        try:
            rows = []
            links = []
            for row in getattr(markup, 'inline_keyboard', []):
                new_row = []
                for button in row:
                    info = button_info(button)
                    if info:
                        buttons.append(info)
                    if info and button.url and urlsplit(button.url).scheme in ('http', 'https') and config.ANALYTICS_LINK_BASE:
                        token = secrets.token_urlsafe(24)
                        links.append((token, uid, screen, info['action'], info['label'], button.url))
                        tokens.append(token)
                        button = button.model_copy(update={'url': config.ANALYTICS_LINK_BASE + '/r/' + token})
                    new_row.append(button)
                rows.append(new_row)
            if links:
                async with asyncio.timeout(2):
                    async with db.pool.acquire() as conn:
                        await conn.executemany('INSERT INTO analytics_links '
                            '(token,tg_id,screen,action,label,destination) VALUES($1,$2,$3,$4,$5,$6)', links)
                updated = method.model_copy(update={'reply_markup': markup.model_copy(update={'inline_keyboard': rows})})
        except Exception:
            # If tracking is unavailable, send the original working links.
            tokens = []
            logging.exception('Link tracking unavailable; sending direct URLs')
        try:
            result = await make_request(bot, updated)
        except Exception:
            await record(uid, 'delivery_error', api, screen=screen)
            raise
        mid = getattr(result, 'message_id', None)
        if mid:
            try:
                async with asyncio.timeout(2):
                    async with db.pool.acquire() as conn:
                        async with conn.transaction():
                            await conn.execute('INSERT INTO analytics_messages(tg_id,message_id,screen,buttons) '
                                'VALUES($1,$2,$3,$4::jsonb) ON CONFLICT DO NOTHING', uid, mid, screen, json.dumps(buttons))
                            if tokens:
                                await conn.execute('UPDATE analytics_links SET message_id=$1 WHERE token=ANY($2::text[])', mid, tokens)
                await record(uid, 'screen_view', screen, screen=screen, message_id=mid,
                             dedupe_key=f'screen:{uid}:{mid}')
            except Exception:
                logging.exception('Screen analytics failed after successful delivery')
        return result


@router.api_route('/r/{token}', methods=['GET', 'HEAD'])
async def open_link(token: str, request: Request):
    headers = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer', 'X-Robots-Tag': 'noindex, nofollow'}
    if not re.fullmatch(r'[A-Za-z0-9_-]{32}', token):
        return Response(status_code=404, headers=headers)
    try:
        async with asyncio.timeout(3):
            async with db.pool.acquire() as conn:
                link = await conn.fetchrow('SELECT * FROM analytics_links WHERE token=$1', token)
    except Exception:
        return Response('Please try this link again shortly.', status_code=503, headers=headers)
    if not link:
        return Response(status_code=404, headers=headers)
    # GETs can include preview agents or forwarded links. These are explicitly
    # reported as link opens, not authenticated Telegram button clicks.
    agent = request.headers.get('user-agent', '').lower()
    preview = any(x in agent for x in ('bot', 'crawler', 'spider', 'preview'))
    purpose = (request.headers.get('purpose', '') + request.headers.get('sec-purpose', '')).lower()
    if request.method == 'GET' and not preview and 'prefetch' not in purpose and link['message_id']:
        # Count once per link per 30-minute bucket; repeated reloads cannot inflate it without bound.
        import time
        await record(link['tg_id'], 'link_open', link['action'], label=link['label'],
                     screen=link['screen'], message_id=link['message_id'],
                     dedupe_key=f'link:{token}:{int(time.time()) // 1800}')
    return RedirectResponse(link['destination'], status_code=302, headers=headers)
