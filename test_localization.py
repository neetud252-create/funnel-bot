"""Offline localization coverage, Telegram serialization and recipient isolation."""
import asyncio
import html
import importlib.util
import os
from pathlib import Path
import re
import types
from unittest.mock import AsyncMock, patch

from aiogram import Bot
from aiogram.methods import AnswerCallbackQuery, CopyMessage, SendAnimation, SendDocument, SendMessage, SendPhoto
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import config
import home_menu
import localization as L
import emoji_theme
import test_signal_flow as H


def sample(source):
    values = dict(uid='123456789', limit=30, premium_limit=50, used=2, left=28,
                  level=config.LEVEL_LABELS_TG['start'], name='Start',
                  icon=config.LEVEL_ICONS_TG['start'], tokens=12, cost=50,
                  balance=12, needed=38, seconds=20, wait='00:30', pair='GBP/USD OTC',
                  expiry='M1', direction=config.SIGNAL_DIRECTIONS[0][0],
                  asset='GBP/USD OTC', timeframe='M1', reason='visible structure',
                  trend='upward', momentum='buyers active',
                  invalidation='visible support breaks', condition='visible support breaks',
                  status=config.MSG_PREMIUM_ACTIVE.format(limit=50),
                  ref='https://example.com/Start?click_id=abc_BUY&src=Guide')
    return source.format(**values)


def check_catalog():
    assert len(L.CODES) == 12 and len(set(L.CODES)) == 12
    labels = [button[0] for screen in config.SCREENS.values()
              for row in screen.get('kb', []) for button in row]
    labels += [button[0] for row in home_menu.keyboard() for button in row]
    labels += [button[0] for name, rows in vars(config).items()
               if name.endswith('_KB') and isinstance(rows, list)
               for row in rows for button in row]
    labels += ['Start Trading', 'I Downloaded My Gift', 'Deposit Now',
               'Register & Get Gift', 'Open Go+', 'Continue', 'Stop broadcast messages']
    # Coverage must fail when a new English screen/phrase is added without translations.
    allowed = re.compile(r'Go Plus|Go\+|Premium|Start|YouTube|OTC|FIN|\b[SM]\d+\b|\b[A-Z]{3}/[A-Z]{3}\b')
    for text in (*L.message_sources(), *labels):
        for part in L._protected.split(text)[::2]:
            residue = re.sub(r'\{[^}]+\}', '', L._phrases.sub('', part))
            assert not re.search('[A-Za-z]', allowed.sub('', residue)), (text, residue)
    for language in L.CODES:
        for source in L.message_sources():
            text = sample(source)
            translated = L.translate_message(text, language)
            assert L._protected.findall(text) == L._protected.findall(translated), (language, source)
            # Template variables, IDs and cash values cannot disappear or change.
            for value in ('123456789', '$50', 'GBP/USD OTC', 'M1', '00:30'):
                if value in text:
                    assert value in translated, (language, source, value)
            assert not re.search(r'\{[a-z_]+\}', translated)
            if language != 'en' and any(L._phrases.search(p) for p in L._protected.split(text)[::2]):
                assert translated != text, (language, source)
        for screen in config.SCREENS.values():
            text = L.translate_message(sample(screen['text']), language)
            plain = html.unescape(re.sub('<[^>]+>', '', text))
            assert len(plain.encode('utf-16-le')) // 2 <= 1024, (language, screen)
        for text in labels:
            assert len(L.translate_parts(text, language, button=True)) <= 64
        for text in ['Language saved.', *home_menu.PENDING.values(),
                     'You have not joined the channel yet. Subscribe first.', config.MSG_DAILY_LIMIT]:
            assert len(L.translate_message(text, language).encode('utf-16-le')) // 2 <= 200
    assert L.translate_message(home_menu.TEXT, 'unknown') == home_menu.TEXT
    # Brand tier names do not turn into the action "Start" in another language.
    assert 'Start' in L.translate_message(sample(config.MSG_LEVEL), 'hi')
    assert L.translate_parts('Start', 'hi', button=True) == 'शुरू करें'


async def check_delivery_and_selector():
    db = H._install_stub_modules()
    bot_mod = H._load_bot()
    for tg_id, language in ((1, 'hi'), (2, 'ru'), (3, 'ar')):
        db._users[tg_id] = dict(db._fresh_row(), language=language,
                               uid='123456789', verified=True, deposit=50, ref_code='keep-me')

    async def save(tg_id, language):
        db._users.setdefault(tg_id, db._fresh_row())['language'] = language
    db.set_language = AsyncMock(side_effect=save)
    state = H.FakeState('registration-in-progress')
    bot = H.FakeBot()
    await bot_mod.home_action(H.FakeCB(1, 'home:language', 10), bot, state)
    assert state.state == 'registration-in-progress'
    markup = bot.calls[-1]['markup']
    assert [len(row) for row in markup.inline_keyboard] == [2, 2, 2, 2, 2, 2, 1]
    selected = [b.callback_data for row in markup.inline_keyboard for b in row if b.text.startswith('✅')]
    assert selected == ['lang:hi']
    initial = dict(db._users[1])
    await bot_mod.choose_language(H.FakeCB(1, 'lang:pt', 10), bot)
    assert await L.language_for(1) == 'pt'
    assert {k:v for k,v in db._users[1].items() if k not in ('language','ui_msg_id')} == {
        k:v for k,v in initial.items() if k not in ('language','ui_msg_id')}
    assert await L.language_for(2) == 'ru'
    db.set_language.reset_mock()
    await bot_mod.choose_language(H.FakeCB(1, 'lang:invalid', 10), bot)
    db.set_language.assert_not_awaited()
    await bot_mod.home_action(H.FakeCB(1, 'home:back', 10), bot, state)
    assert state.state is None and bot.calls[-1]['body'] == home_menu.TEXT
    # /start does not reset the saved choice, quota, verification or first-touch ID.
    with patch.object(bot_mod, '_capture_ref', AsyncMock()):
        await bot_mod.start(H.FakeMessage(1, '/start'), bot, state, types.SimpleNamespace(args=None))
    assert await L.language_for(1) == 'pt' and db._users[1]['ref_code'] == 'keep-me'

    outbound = L.OutboundMiddleware()
    context = L.UserContextMiddleware()
    async def transport(bot, method):
        await asyncio.sleep(0)
        return method
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='💎 Get Bot Access', callback_data='home:access', style='success'),
        InlineKeyboardButton(text='Register & Get Access', url='https://example.com/Start?click_id=abc')]])
    original = SendMessage(chat_id=2, text=home_menu.TEXT, reply_markup=markup, parse_mode='HTML')
    result = await outbound(transport, None, original)
    assert 'Добро пожаловать' in result.text
    assert original.text == home_menu.TEXT and markup.inline_keyboard[0][0].text == '💎 Get Bot Access'
    assert result.reply_markup.inline_keyboard[0][0].callback_data == 'home:access'
    assert result.reply_markup.inline_keyboard[0][0].style == 'success'
    assert result.reply_markup.inline_keyboard[0][1].url == markup.inline_keyboard[0][1].url
    assert result.model_dump(exclude_defaults=True)['reply_markup']['inline_keyboard'][0][0]['text'] == 'Получить доступ'
    assert result.reply_markup.inline_keyboard[0][0].icon_custom_emoji_id == '5427168083074628963'
    assert '5773793517482546530' in result.text

    for language in L.CODES:
        animation = SendAnimation(chat_id=2, animation='banner-animation-id',
                                  caption=home_menu.TEXT, reply_markup=markup, parse_mode='HTML')
        localized = L.localize_method(animation, language)
        assert localized.animation == 'banner-animation-id'
        assert localized.caption == emoji_theme.html(L.translate_message(home_menu.TEXT, language))
        assert '4942823933909926751' in localized.caption
        assert localized.reply_markup.inline_keyboard[0][0].callback_data == 'home:access'
        plain = html.unescape(re.sub('<[^>]+>', '', localized.caption))
        assert len(plain.encode('utf-16-le')) // 2 <= 1024

    async def callback_handler(event, data):
        # Allow other callbacks to overlap. A different recipient still wins over context.
        await asyncio.sleep(0)
        popup = await outbound(transport, None, AnswerCallbackQuery(callback_query_id='123', text='Verified'))
        background = await outbound(transport, None, SendMessage(chat_id=3, text=config.MSG_ENTER_UID))
        return popup.text, background.text
    answers = await asyncio.gather(*(context(callback_handler, H.FakeCB(tg_id, 'check', 1), {})
                                     for tg_id in (1, 2)))
    assert answers[0][0] == L.translate_message('Verified', 'pt')
    assert answers[1][0] == L.translate_message('Verified', 'ru')
    assert answers[0][1] == answers[1][1] == L.translate_message(config.MSG_ENTER_UID, 'ar')
    assert L._callback_user.get() is None
    # Read the DB on delivery, so a delayed job uses a choice made after it was queued.
    await save(2, 'bn')
    result = await outbound(transport, None, original)
    assert result.text == emoji_theme.html(L.translate_message(home_menu.TEXT, 'bn'))
    assert await L.language_for(99999) == 'en'
    with patch.object(db, 'get_user', AsyncMock(side_effect=RuntimeError('offline'))):
        assert await L.language_for(2) == 'en'

    # Run actual verification/gift/UID screen handlers, then check every emitted message.
    await db.set_setting(bot_mod.GIFT_FILE_ID_KEY, 'gift-file-id')
    bot = H.FakeBot()
    await bot_mod._send_gift(bot, 2)
    await bot_mod._show_start_trading(bot, 2)
    db._users[2]['verified'] = False
    await bot_mod._show_start_trading(bot, 2)
    await bot_mod.gift_start(H.FakeCB(2, 'gift:start', 1), bot, state)
    for call in bot.calls:
        if call['kind'] == 'delete':
            continue
        translated = L.translate_message(call['body'], 'bn')
        assert translated != call['body'], call
        method = (SendDocument(chat_id=2, document='gift-file-id', caption=call['body'])
                  if call['kind'] == 'document' else SendMessage(chat_id=2, text=call['body']))
        result = await outbound(transport, None, method)
        assert (getattr(result, 'caption', None) or result.text) == translated
    photo = SendPhoto(chat_id=2, photo='image-file-id', caption=sample(config.SIGNAL_RESULT), parse_mode='HTML')
    result = await outbound(transport, None, photo)
    assert result.photo == photo.photo and 'GBP/USD OTC' in result.caption and 'কিনুন' in result.caption

    # Copied post text is Telegram-owned; translate only its built-in CTA.
    copy = CopyMessage(chat_id=2, from_chat_id=1, message_id=15,
                       reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                           InlineKeyboardButton(text='Open Go+', callback_data='bc:open:campaign')]]))
    result = await outbound(transport, None, copy)
    assert result.from_chat_id == 1 and result.message_id == 15 and result.caption is None
    assert result.reply_markup.inline_keyboard[0][0].text == 'Go+ খুলুন'
    authored = 'My own broadcast: BUY now! Start tomorrow.'
    assert L.translate_message(authored, 'bn') == authored
    # Confirm aiogram accepts the middleware with its real session manager.
    real_bot = Bot('123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi')
    real_bot.session.middleware(outbound)
    await real_bot.session.close()


async def check_storage_contract():
    # Load the real database module separately from the handler test's fake.
    spec = importlib.util.spec_from_file_location('db_language_test', Path(__file__).with_name('db.py'))
    real_db = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {'DATABASE_URL': 'postgresql://unused'}):
        spec.loader.exec_module(real_db)
    assert "ADD COLUMN IF NOT EXISTS language TEXT NOT NULL DEFAULT 'en'" in real_db.SCHEMA
    connection = types.SimpleNamespace(execute=AsyncMock())
    class Acquisition:
        async def __aenter__(self):
            return connection
        async def __aexit__(self, *args):
            pass
    real_db.pool = types.SimpleNamespace(acquire=Acquisition)
    await real_db.set_language(123, 'hi')
    sql, tg_id, language = connection.execute.call_args.args
    assert (tg_id, language) == (123, 'hi')
    assert 'ON CONFLICT (tg_id) DO UPDATE SET language=EXCLUDED.language' in sql
    assert 'verified' not in sql and 'ref_code' not in sql
    connection.execute.reset_mock()
    try:
        await real_db.set_language(123, 'xx')
    except ValueError:
        pass
    else:
        raise AssertionError('Unknown language accepted')
    connection.execute.assert_not_awaited()


async def main():
    check_catalog()
    await check_delivery_and_selector()
    await check_storage_contract()
    print('PASS: 12-language catalog, selector, saved choices, isolation, media captions, callbacks and storage.')


if __name__ == '__main__':
    asyncio.run(main())
