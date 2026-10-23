"""Public interface translations, identifier safety and immediate language switching."""
import asyncio
import html
from html.parser import HTMLParser
import os
import re
import time
from string import Formatter
import unittest
from unittest.mock import AsyncMock, patch
os.environ.setdefault('DATABASE_URL', 'postgresql://unused')

from aiogram import Bot
from aiogram.methods import SendMessage, SendPhoto, SetMyName, SetMyDescription, SetMyShortDescription
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import bot_profile
import chart_analysis
import home_menu
import journey
import localization as L
import market_catalog
import market_signals
import market_progress
import start_here
import verification_reminders
import test_signal_flow as H
from test_chart_signals import VALID
from test_market_data import ASSET, payload
import market_data


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)
    def handle_endtag(self, tag):
        assert self.stack and self.stack.pop() == tag, tag


def plain(text):
    return html.unescape(re.sub(r'<[^>]+>', '', text))


class InterfaceTests(unittest.IsolatedAsyncioTestCase):
    def test_exact_requested_welcome(self):
        self.assertEqual(home_menu.TEXT,
            '👋 <b>Welcome to Pocket IQ</b>\nYour AI-powered chart analysis assistant.\n\n'
            '📊 <b>Choose an asset. Explore the setup.</b>\n'
            'Select your market, asset, and expiration time to get a candlestick chart and BUY or SELL analysis when live data is available.\n\n'
            '🎁 <b>Your first two analyses are free.</b>\n'
            'Try Pocket IQ before deciding whether to set up access.\n\n'
            '👇 <b>Tap “Try 2 Free Analyses” to get started.</b>')

    def test_all_template_languages_markup_and_caption_limits(self):
        for source, translations in L.TEMPLATES.items():
            self.assertEqual(set(translations), set(L.CODES[1:]))
            fields = [x[1] for x in Formatter().parse(source) if x[1] is not None]
            for code, text in translations.items():
                with self.subTest(code=code, source=source[:50]):
                    parser = Tags()
                    parser.feed(text)
                    self.assertFalse(parser.stack)
                    self.assertEqual(sorted(fields), sorted(x[1] for x in Formatter().parse(text) if x[1] is not None))
                    self.assertNotRegex(text, r'ZXQ|ZXSEG|Go\+|Go Plus')
                    self.assertLessEqual(len(plain(text).encode('utf-16-le')) // 2, 1024)
                    if 'Pocket IQ' in source:
                        self.assertIn('Pocket IQ', text)

    def test_all_public_buttons_translate_and_preserve_actions(self):
        for user in ({}, {'uid': '123456789'}, {'verified': True}):
            rows = home_menu.keyboard(user) + start_here.keyboard(user) + journey.dashboard_keyboard()
            for screen in journey.TEXTS:
                rows += journey.screen(screen, user)[1]
            for content in (journey.setup(), journey.account_help(), journey.requirements(),
                    journey.registration('https://example.org/signup'), journey.referral_help(), journey.uid(),
                    journey.deposit(0), journey.ready(), journey.pending(), journey.unlinked(), journey.unlinked(True),
                    journey.premium(), journey.notifications(user)):
                rows += content[1]
            markup = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=b[0], **({'url':b[1][4:]} if b[1].startswith('url:') else {'callback_data':b[1][3:]}))
                for b in row] for row in rows])
            original = SendMessage(chat_id=1, text=home_menu.TEXT, reply_markup=markup, parse_mode='HTML')
            for code in L.CODES[1:]:
                rendered = L.localize_method(original, code)
                for old_row,new_row in zip(markup.inline_keyboard,rendered.reply_markup.inline_keyboard):
                    for old,new in zip(old_row,new_row):
                        label = __import__('emoji_theme').button(old).text
                        self.assertNotEqual(new.text, label, (code,label))
                        self.assertEqual((new.callback_data,new.url),(old.callback_data,old.url))
                        self.assertLessEqual(len(new.text),64)
                self.assertIn(L.translate_parts('Try 2 Free Analyses', code), plain(rendered.text))

    def test_asset_search_expiry_results_and_ai_language(self):
        snapshot = market_data.parse_candles(payload(now=time.time()), ASSET, 60)
        entry = market_catalog.Entry('Home Depot OTC','stocks','otc')
        for code in L.CODES[1:]:
            with self.subTest(code=code):
                text, rows = market_signals.picker((entry,), category='stocks', query='Home',language=code)
                self.assertNotIn('Select a stock', text)
                self.assertNotIn('Search:', text)
                self.assertRegex(text,r'<b>\s*Home\s*</b>')
                self.assertEqual(rows[0][0][0], 'Home Depot OTC')
                markup = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text=entry.name,callback_data='market:a:'+entry.key)]])
                localized = L.localize_method(SendMessage(chat_id=1,text=text,reply_markup=markup),code)
                self.assertEqual(localized.reply_markup.inline_keyboard[0][0].text,entry.name)
                # Already localized prose and opaque AI text are not translated again.
                result = market_signals.result_text(snapshot,dict(VALID,reason='Opaque AI text & <tag>'),3,code)
                self.assertIn('📈 <b>BUY</b>',result)
                self.assertIn('M3',result)
                self.assertIn('Opaque AI text &amp; &lt;tag&gt;',result)
                self.assertNotIn('Expiration:',result)
                self.assertNotIn('Your Signal Is Ready!',result)
                self.assertNotIn('<b>Reason:</b>',result)
                self.assertEqual(L.translate_message(result,code),result)
                seconds = market_signals.result_text(snapshot,VALID,5,code,expiry_unit='s')
                self.assertIn('S5',seconds)
                self.assertNotIn('M5',seconds)
                self.assertNotIn('Expiration:',seconds)
                self.assertIn(chart_analysis.LANGUAGES[code],chart_analysis.request_body(b'\x89PNG\r\n\x1a\n',code)['systemInstruction']['parts'][0]['text'])
                loading = market_progress.Loading(None,1,code,AsyncMock(return_value=True))
                self.assertNotIn(market_progress.ANALYSING,loading.text())
                for message in market_signals.MESSAGES.values():
                    self.assertNotEqual(L.translate_message(message,code),message)

    def test_category_custom_icons_survive_translation(self):
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=market_catalog.category_info(category)[0],
                callback_data=f'market:cat:otc:{category}',style='primary')]
            for category in ('indices','commodities')]+[[
                InlineKeyboardButton(text='🔥 M1',callback_data='market:expire:asset:1',style='primary')]])
        original = SendMessage(chat_id=1,text=market_signals.MARKET_TITLE,reply_markup=markup,parse_mode='HTML')
        for code in L.CODES:
            rendered = L.localize_method(original,code)
            buttons = [row[0] for row in rendered.reply_markup.inline_keyboard]
            self.assertEqual([b.icon_custom_emoji_id for b in buttons],
                             ['5206598117738177151','5436341901290658109','5424972470023104089'])
            self.assertEqual([b.callback_data for b in buttons],
                             [row[0].callback_data for row in markup.inline_keyboard])
            for b in buttons:
                self.assertNotRegex(b.text, r'🛢|🔥|<tg-emoji')
                self.assertEqual(b.style,'primary')
            if code!='en':
                self.assertNotEqual(buttons[0].text,'Indices')
                self.assertNotEqual(buttons[1].text,'Commodities')

    def test_registration_identifiers_and_profile(self):
        ref='https://example.org/signup?click_id=Start_BUY&token='+'x'*130
        text,_=journey.registration(ref)
        for code in L.CODES:
            translated=L.translate_message(text,code)
            self.assertIn(html.escape(ref,quote=True),translated)
            self.assertIn('<code>123456789</code>',translated)
            self.assertNotIn('Go Plus',bot_profile.description(code))
            self.assertLessEqual(len(bot_profile.description(code)),512)
            self.assertLessEqual(len(bot_profile.short_description(code)),120)
            if code!='en':
                self.assertNotIn('Open Pocket Option registration',translated)
                self.assertNotEqual(L.translate_message(verification_reminders.TEXT,code),verification_reminders.TEXT)

    async def test_language_switch_updates_home_and_saved_pin_without_resetting_account(self):
        db=H._install_stub_modules()
        mod=H._load_bot()
        bot=H.FakeBot()
        db._users[55]=dict(db._fresh_row(),language='en',uid='123456789',verified=True,deposit=50,ref_code='keep')
        async def save(uid,code): db._users[uid]['language']=code
        db.set_language=AsyncMock(side_effect=save)
        state=H.FakeState('Market:browsing')
        with patch.object(mod,'_sync_welcome',AsyncMock()) as pin:
            await mod.choose_language(H.FakeCB(55,'lang:hi',1),bot,state)
        self.assertIsNone(state.state)
        self.assertEqual(db._users[55]['language'],'hi')
        self.assertEqual(db._users[55]['uid'],'123456789')
        self.assertTrue(db._users[55]['verified'])
        pin.assert_awaited_once_with(bot,55)
        self.assertEqual(H.last_screen(bot)['body'],home_menu.TEXT)
        self.assertNotIn('Welcome',L.localize_method(SendPhoto(chat_id=55,photo='id',caption=H.last_screen(bot)['body']),'hi').caption)

    def test_translation_updates_invalidate_saved_pin(self):
        user={'language':'hi'}
        before=start_here.signature(user)
        with patch.object(L,'CATALOG_VERSION','new-translation-version'):
            self.assertNotEqual(start_here.signature(user),before)

    async def test_tools_faq_and_old_risk_button(self):
        for verified in (False,True):
            text,rows=journey.screen('tools',{'verified':verified})
            self.assertFalse(any('risk' in b[1] or 'referral' in b[1] for row in rows for b in row))
            faq=journey.TEXTS['faq'].lower()
            self.assertNotIn('referral',faq)
            self.assertNotIn('risk',faq)
        db=H._install_stub_modules();mod=H._load_bot();bot=H.FakeBot()
        db._users[55]=db._fresh_row()
        await mod.flow_action(H.FakeCB(55,'flow:risks',1),bot,H.FakeState())
        self.assertEqual(H.last_screen(bot)['body'],journey.TEXTS['tools'])

    async def test_public_profile_names_and_descriptions(self):
        # Exercise the real SDK methods and models; mock only network transport.
        bot=Bot('123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi')
        try:
            with patch.object(bot.session,'make_request',AsyncMock(return_value=True)) as send:
                await bot_profile.sync(bot)
            methods=[call.args[1] for call in send.await_args_list]
            self.assertEqual(len(methods),39)
            for kind in (SetMyName,SetMyDescription,SetMyShortDescription):
                selected=[m for m in methods if isinstance(m,kind)]
                self.assertEqual({m.language_code for m in selected},{'',*L.CODES})
                self.assertEqual(len(selected),13)
            self.assertTrue(all(m.name=='Pocket IQ' for m in methods if isinstance(m,SetMyName)))
            self.assertTrue(all(m.short_description for m in methods if isinstance(m,SetMyShortDescription)))
        finally:
            await bot.session.close()


if __name__=='__main__': unittest.main()
