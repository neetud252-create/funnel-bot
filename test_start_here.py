"""Pinned card migration, real callback routes, verification and failure recovery."""
import asyncio
from decimal import Decimal
import unittest
from unittest.mock import AsyncMock, patch
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import EditMessageCaption, EditMessageText, EditMessageMedia
from aiogram.types import InputMediaVideo
import start_here
import test_signal_flow as H


class StartHereTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = H._install_stub_modules()
        self.mod = H._load_bot()
        self.bot, self.state = H.FakeBot(), H.FakeState()
        self.uid = 74001
        self.db.pool = object()
        async def trial_status(pool, uid):
            return self.db._users[uid]['verified'], 2
        status = patch.object(self.mod.chart_signals.trial, 'status', side_effect=trial_status)
        status.start()
        self.addCleanup(status.stop)

    async def start(self):
        await self.mod.start(H.FakeMessage(self.uid, '/start'), self.bot, self.state)
        return self.db._users[self.uid]

    def labels(self):
        return [[b.text for b in row] for row in self.bot.edits[-1]['reply_markup'].inline_keyboard]

    async def tap(self, action, anchor):
        cb = H.FakeCB(self.uid, action, anchor)
        handler = self.mod.home_action if action.startswith('home:') else self.mod.flow_action
        await handler(cb, self.bot, self.state)
        return H.last_screen(self.bot)

    async def test_enhanced_layout_and_all_destinations(self):
        row = await self.start()
        anchor = row['welcome_message_id']
        edit = self.bot.edits[-1]
        self.assertEqual(edit['caption'], start_here.TEXT)
        self.assertLess(len(start_here.TEXT), 1024)
        self.assertEqual(self.labels(), [
            ['🎯 Try Free Analysis'], ['🚀 Account Setup'],
            ['📖 How It Works', '📢 Community'], ['⭐ Reviews', '💬 Support']])
        self.assertEqual(self.bot.pins, [(self.uid, anchor, True)])
        self.assertEqual(self.bot.calls[-1]['asset'], 'assets/home_banner_loop.mp4')
        callbacks = [b.callback_data for r in edit['reply_markup'].inline_keyboard for b in r]
        self.assertEqual(callbacks, ['home:test', 'home:access', 'flow:how',
                                     'flow:community', 'flow:reviews', 'flow:support'])
        self.assertEqual([b.style for r in edit['reply_markup'].inline_keyboard for b in r],
                         ['success', 'primary', 'primary', 'primary', 'danger', 'danger'])
        # Exercise the actual handlers, including the trial gate and configured links.
        trial = await self.tap('home:test', anchor)
        self.assertIn('Select market type', trial['body'])
        setup = await self.tap('home:access', anchor)
        self.assertIn('Let’s activate your Pocket IQ access!', setup['body'])
        self.assertFalse(row['verified'])
        for callback in ('flow:how', 'flow:community', 'flow:reviews', 'flow:support',
                         'flow:tools', 'home:language'):
            screen = await self.tap(callback, anchor)
            self.assertTrue(screen['body'] and screen['markup'], callback)
        await self.db.set_setting(self.mod.GIFT_FILE_ID_KEY, 'welcome-workbook-id')
        await self.tap('home:sheet', anchor)
        self.assertEqual(self.bot.calls[-1]['kind'], 'document')
        self.assertEqual(self.bot.calls[-1]['asset'], 'welcome-workbook-id')
        self.assertFalse(any(c['kind'] == 'delete' and c['id'] == anchor for c in self.bot.calls))
        self.assertEqual(len(self.bot.edits), 1)

    async def test_live_verification_switches_buttons_without_restart(self):
        row = await self.start()
        anchor = row['welcome_message_id']
        row.update(uid='123456789', account_kind='existing', eligibility_confirmed_at='now')
        with patch.object(self.mod, '_panel_verdict', AsyncMock(return_value=(
                self.mod.config.VERIFY_GRANTED, Decimal('50')))):
            await self.tap('flow:recheck', anchor)
        self.assertTrue(row['verified'])
        self.assertEqual(self.labels()[:2], [['📊 Get Signals'], ['🏠 My Dashboard']])
        self.assertEqual(self.bot.edits[-1]['message_id'], anchor)
        self.assertEqual(len(self.bot.pins), 1)
        chart = await self.tap('home:test', anchor)
        self.assertNotIn('two free', chart['body'].lower())
        dashboard = await self.tap('home:access', anchor)
        self.assertIn('menu:signal', [b.callback_data for r in dashboard['markup'].inline_keyboard for b in r])
        self.mod = H._load_bot()
        await self.start()
        self.assertEqual(H.last_screen(self.bot)['asset'], 'assets/menu.png')
        self.assertEqual(row['welcome_message_id'], anchor)
        self.assertEqual(len(self.bot.edits), 2)  # persisted hash survives restart
        self.assertEqual(len(self.bot.pins), 1)
        await self.db.unverify(self.uid)
        await self.start()
        self.assertEqual(self.labels()[:2], [['🎯 Try Free Analysis'], ['🚀 Account Setup']])

    async def test_startup_upgrades_old_pins_in_place_with_pagination(self):
        for uid in (12, 13, 14):
            self.db._users[uid] = dict(self.db._fresh_row(), welcome_message_id=uid + 200,
                                       welcome_pinned_at='previous', verified=uid == 13)
        self.db._users[15] = self.db._fresh_row()  # Never contact unstarted accounts.
        original = self.db.welcome_users_after
        async def small_page(after):
            return await original(after, 2)
        with patch.object(self.db, 'welcome_users_after', side_effect=small_page), \
             patch.object(self.mod.asyncio, 'sleep', AsyncMock()):
            await self.mod._refresh_saved_welcomes(self.bot)
        self.assertEqual([e['message_id'] for e in self.bot.edits], [212, 213, 214])
        self.assertTrue(all(e['kind'] == 'media' and isinstance(e['media'], InputMediaVideo)
                            and e['media'].supports_streaming for e in self.bot.edits))
        self.assertEqual(self.bot.edits[1]['reply_markup'].inline_keyboard[1][0].text, '🏠 My Dashboard')
        self.assertEqual(self.bot.calls, [])
        self.assertEqual(self.bot.pins, [])
        await self.mod._refresh_saved_welcomes(self.bot)
        self.assertEqual(len(self.bot.edits), 3)

    async def test_text_fallback_and_unchanged_retry_persist_hash(self):
        row = self.db._users[self.uid] = dict(self.db._fresh_row(), welcome_message_id=90,
                                             welcome_pinned_at='previous',
                                             welcome_content_hash=start_here.MEDIA_VERSION + 'old-copy')
        no_caption = TelegramBadRequest(method=EditMessageCaption(chat_id=self.uid, message_id=90),
                                         message='Bad Request: there is no caption in the message to edit')
        with patch.object(self.bot, 'edit_message_caption', AsyncMock(side_effect=no_caption)):
            self.assertEqual(await self.mod._sync_welcome(self.bot, self.uid), 'updated')
        self.assertEqual(self.bot.edits[-1]['text'], start_here.TEXT)
        row['welcome_content_hash'] = start_here.MEDIA_VERSION + 'old-copy'
        unchanged = TelegramBadRequest(method=EditMessageText(chat_id=self.uid, message_id=90, text='x'),
                                        message='Bad Request: message is not modified')
        with patch.object(self.bot, 'edit_message_caption', AsyncMock(side_effect=no_caption)), \
             patch.object(self.bot, 'edit_message_text', AsyncMock(side_effect=unchanged)):
            self.assertEqual(await self.mod._sync_welcome(self.bot, self.uid), 'updated')
        self.assertEqual(row['welcome_content_hash'], start_here.signature(row))

    async def test_failed_edit_preserves_pin_and_retries_on_interaction(self):
        row = await self.start()
        anchor = row['welcome_message_id']
        row.update(verified=True, uid='123456789')
        previous_hash = row['welcome_content_hash']
        with patch.object(self.bot, 'edit_message_caption', AsyncMock(side_effect=RuntimeError('offline'))):
            screen = await self.tap('home:access', anchor)
        self.assertTrue(screen['markup'])
        self.assertEqual(row['welcome_content_hash'], previous_hash)
        await self.tap('home:access', anchor)
        self.assertEqual(self.labels()[:2], [['📊 Get Signals'], ['🏠 My Dashboard']])
        self.assertEqual(len(self.bot.pins), 1)
        # Deleted successful pins must not be re-created or forced back.
        row['welcome_content_hash'] = None
        missing = TelegramBadRequest(method=EditMessageCaption(chat_id=self.uid, message_id=anchor),
                                      message='Bad Request: message to edit not found')
        with patch.object(self.bot, 'edit_message_media', AsyncMock(side_effect=missing)):
            await self.start()
        self.assertEqual(len(self.bot.pins), 1)
        self.assertEqual(row['welcome_message_id'], anchor)
        self.assertIsNone(row['welcome_content_hash'])

    async def test_rate_limit_backoff_and_concurrent_refresh(self):
        row = self.db._users[self.uid] = dict(self.db._fresh_row(), welcome_message_id=90,
                                             welcome_pinned_at='previous',
                                             welcome_content_hash=start_here.MEDIA_VERSION + 'old-copy')
        limited = TelegramRetryAfter(method=EditMessageCaption(chat_id=self.uid, message_id=90),
                                      message='Too Many Requests', retry_after=2)
        with patch.object(self.bot, 'edit_message_caption', AsyncMock(side_effect=[limited, True])) as edit, \
             patch.object(self.mod.asyncio, 'sleep', AsyncMock()) as sleep:
            await self.mod._refresh_saved_welcomes(self.bot)
        self.assertEqual(edit.await_count, 2)
        self.assertEqual(sleep.await_args_list[0].args, (3,))
        row['welcome_content_hash'] = None
        await asyncio.gather(*(self.mod._sync_welcome(self.bot, self.uid) for _ in range(3)))
        self.assertEqual(len(self.bot.edits), 1)
        self.assertEqual(self.bot.calls, [])
        self.assertEqual(self.bot.pins, [])

    async def test_emoji_only_change_refreshes_existing_pin_once(self):
        row = await self.start()
        anchor, old_hash = row['welcome_message_id'], row['welcome_content_hash']
        with patch.dict(start_here.emoji_theme.ICONS, {'pin': '1234567890123456789'}):
            await self.mod._sync_welcome(self.bot, self.uid)
            self.assertNotEqual(row['welcome_content_hash'], old_hash)
            await self.mod._sync_welcome(self.bot, self.uid)
        self.assertEqual(len(self.bot.edits), 2)
        self.assertEqual(self.bot.edits[-1]['message_id'], anchor)
        self.assertEqual(len(self.bot.pins), 1)

    async def test_video_upgrade_retries_expired_cache_without_sending_or_repinning(self):
        row = self.db._users[self.uid] = dict(self.db._fresh_row(), welcome_message_id=90,
                                             welcome_pinned_at='previous', welcome_content_hash='legacy-hash')
        key = self.mod.home_menu.VIDEO
        digest = self.mod.content_hash(self.mod.asset_path(key, 'mp4'))
        self.mod._video_cache[key] = ('expired-video-id', digest)
        rejected = TelegramBadRequest(method=EditMessageMedia(
            chat_id=self.uid, message_id=90, media=InputMediaVideo(media='expired-video-id')),
            message='Bad Request: wrong file identifier')
        with patch.object(self.bot, 'edit_message_media', AsyncMock(side_effect=[rejected, True])) as edit:
            self.assertEqual(await self.mod._sync_welcome(self.bot, self.uid), 'updated')
        self.assertEqual(edit.await_count, 2)
        first, second = [call.kwargs['media'] for call in edit.await_args_list]
        self.assertEqual(first.media, 'expired-video-id')
        self.assertEqual(str(second.media.path), 'assets/home_banner_loop.mp4')
        self.assertEqual((second.width, second.height, second.duration), (1280, 720, 6))
        self.assertEqual(row['welcome_content_hash'], start_here.signature(row))
        self.assertEqual(self.bot.calls, [])
        self.assertEqual(self.bot.pins, [])

    async def test_failed_or_missing_video_is_not_marked_migrated(self):
        row = self.db._users[self.uid] = dict(self.db._fresh_row(), welcome_message_id=90,
                                             welcome_pinned_at='previous', welcome_content_hash='legacy-hash')
        with patch.object(self.bot, 'edit_message_media', AsyncMock(side_effect=RuntimeError('offline'))):
            self.assertEqual(await self.mod._sync_welcome(self.bot, self.uid), 'unavailable')
        self.assertEqual(row['welcome_content_hash'], 'legacy-hash')
        with patch.object(self.mod, 'media_missing', return_value=True):
            self.assertEqual(await self.mod._sync_welcome(self.bot, self.uid), 'unavailable')
        self.assertEqual(self.bot.edits[-1]['caption'], start_here.TEXT)
        self.assertEqual(row['welcome_content_hash'], 'legacy-hash')
        self.assertEqual(await self.mod._sync_welcome(self.bot, self.uid), 'updated')
        self.assertEqual(self.bot.edits[-1]['kind'], 'media')
        self.assertEqual(self.bot.pins, [])


if __name__ == '__main__':
    unittest.main()
