"""Offline localization of built-in UI; recipient language is stored in Postgres.

Only registered bot messages are translated. Copied broadcast posts, account IDs,
URLs, Telegram HTML, callback payloads and media contents are never rewritten.
"""
from contextvars import ContextVar
from pathlib import Path
from string import Formatter
import logging
import re
import json
import hashlib
import emoji_theme

LANGUAGES = (
    ('en', 'English 🇬🇧'), ('ru', 'Russian 🇷🇺'),
    ('uk', 'Ukrainian 🇺🇦'), ('hi', 'Hindi 🇮🇳'),
    ('bn', 'Bengali 🇧🇩'), ('ur', 'Urdu 🇵🇰'),
    ('vi', 'Vietnamese 🇻🇳'), ('id', 'Indonesian 🇮🇩'),
    ('tr', 'Turkish 🇹🇷'), ('es', 'Spanish 🇪🇸'),
    ('ar', 'Arabic 🇸🇦'), ('pt', 'Portuguese 🇵🇹'),
)
CODES = tuple(code for code, _ in LANGUAGES)
SELECTOR_TEXT = '🌐 <b>Select Bot Language</b>\n\nChoose your preferred language for signals and messages:'
EXTRA_MESSAGES = (
    SELECTOR_TEXT, 'Language saved.', 'Please choose a language from the list.',
    '🎁 <b>Account confirmed — your free gift is ready.</b>\n\n'
    'Download the Apex Trader Money Management Excel sheet, then tap the button below.',
    '✅ <b>Your account and deposit are verified.</b>\n\nTap Start Trading to open Pocket IQ.',
    '🚀 <b>Your gift is unlocked.</b>\n\nTap Start Trading. Deposit at least '
    '<b>${cost}</b>, then send your account ID again so the bot can confirm your balance and unlock Pocket IQ.',
    '💰 <b>Deposit and verify</b>\n\nAdd <b>${cost}</b> or more to your trading account. '
    'After depositing, send your account ID here again.',
    'Gift confirmed', 'Verified', 'You have not joined the channel yet. Subscribe first.',
    '🔒 Locked. Coming soon', 'Coming soon 🚀', '<b>Real results</b>',
    '⏳ <b>Checking account</b> <code>{uid}</code>…',
    '❗ Your account ID must be <b>numbers only</b> (5–15 digits). Example: <b>123456789</b>',
    'Pocket IQ update', 'Broadcast messages enabled.',
    'Broadcast messages stopped. Bot access is unchanged. /subscribe to resume.',
    'Broadcast messages stopped',
    'Open Pocket IQ in a private chat.',
    'Reminders and broadcasts stopped. Bot access is unchanged.',
    'Open Pocket Option registration',
    'Link unavailable — tap Create New Account below for support.',
)

_catalog = {}
for file in sorted((Path(__file__).parent / 'locales').glob('*.tsv')):
    for number, line in enumerate(file.read_text(encoding='utf-8').splitlines(), 1):
        if not line or line.startswith('#'):
            continue
        fields = line.split('|')
        if len(fields) != len(CODES) or any(not field for field in fields):
            raise ValueError(f'Invalid translation row: {file.name}:{number}')
        if fields[0] in _catalog:
            raise ValueError(f'Duplicate translation: {fields[0]}')
        _catalog[fields[0]] = dict(zip(CODES, fields))
if not _catalog:
    raise ValueError('Translation catalog is missing')

_protected = re.compile(r'(<code>.*?</code>|<pre>.*?</pre>|<[^>]+>|'
                        r'https?://[^\s<>]+|@[A-Za-z0-9_]+|(?<!\w)/[a-z_]+\b)', re.S)


def translate_parts(text, language, *, button=False):
    """Translate known prose without touching markup or opaque identifiers."""
    if not text or language == 'en' or language not in CODES:
        return text
    if text in TEMPLATES:
        return TEMPLATES[text][language]
    # Exact built-in phrases can contain commands (e.g. /privacy). Translate
    # the whole known phrase before splitting opaque user-supplied content.
    if text in _catalog and (text != 'Start' or button):
        return _catalog[text][language]
    def replacement(match):
        key = match.group()
        # Start/Premium are tier names; Start is a verb only on a button.
        if key == 'Start' and not button:
            return key
        translated = _catalog[key][language]
        if key.startswith('s, then'):
            translated = ' ' + translated
        return translated
    return ''.join(part if index % 2 else _phrases.sub(replacement, part)
        for index, part in enumerate(_protected.split(text)))


def message_sources():
    import config
    import home_menu
    import chart_signals
    import journey
    import start_here
    import market_signals
    import verification_reminders
    import elena_copy
    result = list(EXTRA_MESSAGES) + [home_menu.TEXT, home_menu.SHEET_CAPTION,
                                       home_menu.SHEET_UNAVAILABLE] + list(home_menu.PENDING.values())
    result.extend(chart_signals.SOURCES)
    result.extend(journey.SOURCES)
    result.extend(elena_copy.SOURCES)
    result.extend((start_here.TEXT, verification_reminders.TEXT, *market_signals.SOURCES))
    result.extend(journey.notifications({'broadcast_opt_out': value})[0] for value in (False, True))
    result.extend(screen['text'] for screen in config.SCREENS.values())
    result.extend(value for name, value in vars(config).items()
                  if isinstance(value, str) and value and (
                      name.startswith('MSG_') and not name.startswith(('MSG_ADMIN_', 'MSG_TOKENS_'))
                      or name in ('SIGNAL_ANALYZING', 'SIGNAL_RESULT', 'SIGNAL_CHART')))
    return tuple(dict.fromkeys(result))


def _compile_source(source):
    # Formatting slots may contain a nested status, level emoji or referral URL.
    return re.compile(''.join(re.escape(literal) + (r'.*?' if field is not None else '')
                             for literal, field, _, _ in Formatter().parse(source)), re.S)


_sources = None

# Whole templates preserve grammar across HTML emphasis and protect interpolated
# values (asset names, user searches, URLs, IDs, amounts and AI-generated prose).
TEMPLATES = {}
for _template_name in ('interface.json', 'activation.json'):
    _template_file = Path(__file__).parent / 'locales' / _template_name
    TEMPLATES.update(json.loads(_template_file.read_text(encoding='utf-8')))
_template_patterns = []
for source, translations in TEMPLATES.items():
    if set(translations) != set(CODES[1:]) or not all(translations.values()):
        raise ValueError('Incomplete interface translation')
    fields = {field for _, field, _, _ in Formatter().parse(source) if field is not None}
    for translated in translations.values():
        if {field for _, field, _, _ in Formatter().parse(translated) if field is not None} != fields:
            raise ValueError('Translation changed template fields')
    seen, pattern = set(), ''
    for literal, field, _, _ in Formatter().parse(source):
        pattern += re.escape(literal)
        if field is not None:
            pattern += f'(?P={field})' if field in seen else f'(?P<{field}>.*?)'
            seen.add(field)
    _template_patterns.append((re.compile(pattern, re.S), translations))

# Native button icons are removed before their labels are translated. Include
# standalone interface labels in phrase lookup as well as exact-template lookup.
for source, translations in TEMPLATES.items():
    if '\n' not in source and '<' not in source and '{' not in source:
        _catalog.setdefault(source, {'en': source, **translations})
# Longest first prevents a short button label from replacing part of a sentence.
_phrases = re.compile(r'(?<![^\W\d_])(?:' + '|'.join(
    re.escape(key) for key in sorted(_catalog, key=len, reverse=True)) + r')(?!\w)')
CATALOG_VERSION = hashlib.sha256(json.dumps([_catalog, TEMPLATES],
    ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def translate_message(text, language):
    global _sources
    if not text or language == 'en' or language not in CODES:
        return text
    if text in TEMPLATES:
        return TEMPLATES[text][language]
    for pattern, translations in _template_patterns:
        if match := pattern.fullmatch(text):
            values = match.groupdict()
            if 'ref' in values:
                # Long tracking URLs use this built-in anchor label. Translate
                # the visible label only; keep href and other dynamic values.
                label = 'Open Pocket Option registration'
                values['ref'] = values['ref'].replace('>' + label + '</a>',
                    '>' + translate_parts(label, language) + '</a>')
                fallback = 'Link unavailable — tap Create New Account below for support.'
                if values['ref'] == fallback:
                    values['ref'] = translate_parts(fallback, language)
            return translations[language].format(**values)
    if _sources is None:
        _sources = tuple(_compile_source(source) for source in message_sources())
    if any(pattern.fullmatch(text) for pattern in _sources):
        return translate_parts(text, language)
    return text


async def language_for(tg_id):
    import db
    if not isinstance(tg_id, int) or tg_id <= 0:
        return 'en'
    try:
        user = await db.get_user(tg_id)
        language = user.get('language', 'en') if user else 'en'
        return language if language in CODES else 'en'
    except Exception:
        logging.exception('Language lookup failed for tg_id=%s; using English', tg_id)
        return 'en'


def selector_keyboard(language):
    buttons = [(('✅ ' if code == language else '') + name, 'cb:lang:' + code, 'success')
               for code, name in LANGUAGES]
    return [buttons[index:index + 2] for index in range(0, len(buttons), 2)] + [
        [('⬅️ Back', 'cb:home:back', 'success')]]


_callback_user = ContextVar('callback_language_user', default=None)


class UserContextMiddleware:
    async def __call__(self, handler, event, data):
        user = getattr(event, 'from_user', None)
        token = _callback_user.set(user.id if user else None)
        try:
            return await handler(event, data)
        finally:
            _callback_user.reset(token)


def localize_method(method, language):
    """Copy outbound models; shared keyboards and cached English stay immutable."""
    updates = {}
    media = getattr(method, 'media', None)
    if getattr(media, 'caption', None) is not None:
        updates['media'] = localize_method(media, language)
    for field in ('text', 'caption'):
        value = getattr(method, field, None)
        if isinstance(value, str):
            translated = translate_message(value, language)
            # Telegram callback alerts are plain text. Only HTML messages/captions
            # accept tg-emoji entities; keep their existing plain-text fallbacks.
            if getattr(method, 'parse_mode', None) == 'HTML':
                translated = emoji_theme.html(translated)
            if translated != value:
                updates[field] = translated
    markup = getattr(method, 'reply_markup', None)
    if markup and hasattr(markup, 'inline_keyboard'):
        rows = []
        for row in markup.inline_keyboard:
            buttons = []
            for button in row:
                button = emoji_theme.button(button)
                callback = button.callback_data or ''
                # Language names stay recognizable from any selected language.
                # Admin campaign controls keep their original wording.
                skip = callback.startswith(('lang:', 'an:', 'market:a:', 'pair:')) or (
                    callback.startswith('bc:') and not callback.startswith(('bc:open:', 'bc:stop')))
                text = button.text if skip else translate_parts(button.text, language, button=True)
                buttons.append(button.model_copy(update={'text': text}))
            rows.append(buttons)
        updates['reply_markup'] = markup.model_copy(update={'inline_keyboard': rows})
    return method.model_copy(update=updates) if updates else method


class OutboundMiddleware:
    async def __call__(self, make_request, bot, method):
        has_content = any(isinstance(getattr(method, field, None), str)
                          for field in ('text', 'caption')) or getattr(method, 'reply_markup', None) or (
                              isinstance(getattr(getattr(method, 'media', None), 'caption', None), str))
        if not has_content:
            return await make_request(bot, method)
        # Explicit recipient ALWAYS wins, especially in delayed jobs/broadcasts.
        target = getattr(method, 'chat_id', None)
        if target is None and method.__api_method__ == 'answerCallbackQuery':
            target = _callback_user.get()
        language = await language_for(target)
        return await make_request(bot, localize_method(method, language))
