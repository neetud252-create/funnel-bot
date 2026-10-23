"""Immediate feedback and request-local context for ordinary menu buttons."""
from contextvars import ContextVar
import logging

_message = ContextVar('navigation_message', default=None)
_answered = ContextVar('navigation_answered', default=None)


def fast_ack(data):
    # These routes already acknowledge with an empty answer. Validation, quota,
    # language and verification alerts retain their own handler's answer.
    return (data.startswith(('flow:', 'go:')) or
            data in ('home:access', 'home:back', 'home:language', 'home:test',
                     'home:sheet', 'menu:signal', 'reg:enter_uid', 'noop') or
            (data.startswith('market:') and data.split(':')[1] in
             ('open', 'book', 'cat', 'list', 'a', 'all', 'noop', 'locked')))


def message_for(tg_id, message_id):
    message = _message.get()
    if (message and getattr(message.chat, 'id', None) == tg_id and
            message.message_id == message_id):
        return message
    return None


class CallbackMiddleware:
    async def __call__(self, handler, event, data):
        message = getattr(event, 'message', None)
        private = (message and message.chat.type == 'private' and
                   message.chat.id == event.from_user.id)
        context = _message.set(message if private else None)
        answered = _answered.set(None)
        try:
            if private and fast_ack(event.data or ''):
                try:
                    await event.answer()
                except Exception as exc:
                    # An expired callback must not prevent opening its menu.
                    logging.debug('Early callback acknowledgement: %s', type(exc).__name__)
                else:
                    _answered.set(event.id)
            return await handler(event, data)
        finally:
            _answered.reset(answered)
            _message.reset(context)


class AnswerMiddleware:
    async def __call__(self, make_request, bot, method):
        if (method.__api_method__ == 'answerCallbackQuery' and
                method.callback_query_id == _answered.get() and
                not method.text and not method.show_alert and not method.url):
            return True
        return await make_request(bot, method)
