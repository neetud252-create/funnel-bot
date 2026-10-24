"""One-time Elena contact gate, separate from trading-account entitlements."""
import logging
import os
from urllib.parse import quote
import config
import db
import elena_copy as copy
import elena_store as store

USERNAME = 'elitecsupport'
BOT_USERNAME = 'goplusaitrader_bot'
PHOTO = 'activate_account'
DRAFT = 'Elena, activate my Pocket IQ account access.'
_show_gate = None
_open_interface = None


def enabled():
    return all(os.getenv(key, '1').lower() in ('1', 'true', 'yes', 'on')
               for key in ('ELENA_ENABLED', 'ELENA_ACTIVATION_ENABLED'))


def chat_url(*, draft=None):
    # Open Telegram's chat directly. Only the public username and optional
    # message draft are included; no redirect or attribution identifiers.
    url = f'tg://resolve?domain={USERNAME}'
    return url + f'&text={quote(draft, safe="")}' if draft else url


def keyboard():
    return [[(copy.ACTIVATE_BUTTON,
              'url:' + chat_url(draft=DRAFT), 'primary')]]


async def required(tg_id):
    if not enabled():
        return False
    saved = await store.session(tg_id)
    return not current(saved)


def current(saved):
    # Only activation in this release counts, including for verified users and
    # earlier Elena contacts. Historical messages never bypass the new gate.
    return bool(saved and saved.get('activated_at')
                and saved.get('activation_version') == store.ACTIVATION_VERSION)


def configure(show_gate, open_interface):
    global _show_gate, _open_interface
    _show_gate, _open_interface = show_gate, open_interface


async def deliver(bot, tg_id):
    """Open the normal bot, with a durable lease to suppress duplicate updates."""
    if not enabled() or _open_interface is None:
        return
    claimed = await store.claim_activation(tg_id)
    if not claimed:
        return
    try:
        if await _open_interface(bot, tg_id):
            await store.activation_opened(tg_id)
    except Exception as exc:
        # The return link and /start still work. The worker retries a failed
        # normal-bot delivery at most three times, including after a restart.
        logging.warning('Activation interface delivery unavailable (%s)', type(exc).__name__)


class GateMiddleware:
    async def __call__(self, handler, event, data):
        message = getattr(event, 'message', None) or event
        user = getattr(event, 'from_user', None)
        chat = getattr(message, 'chat', None)
        callback = getattr(event, 'data', None)
        command = (getattr(event, 'text', '') or '').split(maxsplit=1)
        command = command[0].split('@')[0].lower() if command else ''
        normal = (user and not user.is_bot and chat and chat.type == 'private'
                  and chat.id == user.id and not getattr(message, 'business_connection_id', None))
        # /start captures attribution and checks the gate under its screen lock.
        # Always allow notification opt-out and leave operator/business controls alone.
        exempt = (command in ('/start', '/subscribe', '/unsubscribe') or callback in
                  ('bc:stop', 'vr:stop', 'elena:stop', 'elena:later', 'flow:notifications_off')
                  or (user and user.id in config.ADMIN_IDS and
                      (command.startswith('/') or (callback or '').startswith(('an:', 'bc:')))))
        if not normal or exempt or not await required(user.id):
            return await handler(event, data)
        await db.touch_user(user.id, user.username)
        if state := data.get('state'):
            await state.clear()
        if callback is not None:
            await event.answer()
        await _show_gate(data['bot'], user.id)
