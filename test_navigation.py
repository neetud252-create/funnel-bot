"""Button latency, in-place updates and fallback lifecycle without network calls."""
import asyncio
import os
import types
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault('DATABASE_URL', 'postgresql://unused')
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, EditMessageMedia, SendPhoto
import navigation as N
import localization
import test_signal_flow as H


def callback(message, action='market:book:otc'):
    return types.SimpleNamespace(message=message, data=action, id='tap',
        from_user=types.SimpleNamespace(id=12), answer=AsyncMock())


class NavigationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = H._install_stub_modules()
        self.mod = H._load_bot()
        self.bot = H.FakeBot()
        await self.db.set_ui_msg(12, 100)
        self.message = H.FakeMsg(100, caption='Old menu')
        self.message.chat.id = 12
        self.message.photo = [types.SimpleNamespace(file_id='old-photo')]
        self.rows = [[('« Back', 'cb:market:open', 'danger')]]

    async def click(self, key='asset_category', text='Choose a category', **kwargs):
        async def handler(event, data):
            return await self.mod.render(self.bot, 12, key, text, self.rows, **kwargs)
        return await N.CallbackMiddleware()(handler, callback(self.message), {})

    async def test_acknowledges_before_handler_and_skips_second_empty_answer(self):
        event = callback(self.message)
        request = AsyncMock(return_value=True)
        async def handler(event, data):
            event.answer.assert_awaited_once()
            self.assertIs(N.message_for(12, 100), self.message)
            await N.AnswerMiddleware()(request, None, AnswerCallbackQuery(callback_query_id='tap'))
            request.assert_not_awaited()
            await N.AnswerMiddleware()(request, None, AnswerCallbackQuery(
                callback_query_id='tap', text='Please choose an asset.', show_alert=True))
            request.assert_awaited_once()
        await N.CallbackMiddleware()(handler, event, {})
        self.assertIsNone(N.message_for(12, 100))
        await N.AnswerMiddleware()(request, None, AnswerCallbackQuery(callback_query_id='tap'))
        self.assertEqual(request.await_count, 2)
        for action in ('market:expire:a:1', 'check_sub', 'lang:hi', 'market:run:a:60'):
            event = callback(self.message, action)
            await N.CallbackMiddleware()(AsyncMock(), event, {})
            event.answer.assert_not_awaited()

    async def test_photo_menu_needs_one_edit_and_no_send_delete_or_ui_write(self):
        self.mod._photo_cache['asset_category'] = ('cached-category',
            self.mod.content_hash(self.mod.asset_path('asset_category', 'jpg')))
        with patch.object(self.db, 'set_ui_msg', AsyncMock()) as save:
            result = await self.click()
            save.assert_not_awaited()
        self.assertEqual(result.message_id, 100)
        self.assertEqual(self.bot.calls, [])
        self.assertEqual(len(self.bot.edits), 1)
        edit = self.bot.edits[0]
        self.assertEqual(edit['media'].media, 'cached-category')
        self.assertEqual(edit['media'].caption, 'Choose a category')
        self.assertEqual(edit['reply_markup'].inline_keyboard[0][0].callback_data, 'market:open')
        self.assertEqual((await self.db.get_user(12))['ui_msg_id'], 100)
        # Edited payloads still pass through the same localization and emoji path.
        payload = EditMessageMedia(chat_id=12, message_id=100, media=edit['media'],
                                   reply_markup=edit['reply_markup'])
        translated = localization.localize_method(payload, 'hi')
        self.assertNotEqual(translated.reply_markup.inline_keyboard[0][0].text, '« Back')
        self.assertEqual(translated.reply_markup.inline_keyboard[0][0].callback_data, 'market:open')

    async def test_same_photo_page_and_stale_photo_snapshot_always_set_target_artwork(self):
        for key in ('currency_pair', 'expiration_time', 'currency_pair'):
            await self.click(key)
        self.assertEqual(len(self.bot.edits), 3)
        self.assertEqual(self.bot.calls, [])
        self.assertEqual(str(self.bot.edits[-1]['media'].media.path), 'assets/currency_pair.png')

    async def test_duplicate_tap_is_success_without_new_card(self):
        self.bot.edit_message_media = AsyncMock(side_effect=TelegramBadRequest(
            method=EditMessageMedia(chat_id=12, message_id=100, media={
                'type':'photo','media':'cached'}), message='Bad Request: message is not modified'))
        self.assertEqual((await self.click()).message_id, 100)
        self.assertEqual(self.bot.calls, [])

    async def test_missing_message_falls_back_to_send_before_cleanup(self):
        self.bot.edit_message_media = AsyncMock(side_effect=TelegramBadRequest(
            method=EditMessageMedia(chat_id=12, message_id=100, media={
                'type':'photo','media':'cached'}), message='Bad Request: message to edit not found'))
        result = await self.click()
        self.assertEqual([c['kind'] for c in self.bot.calls], ['photo', 'delete'])
        self.assertEqual((await self.db.get_user(12))['ui_msg_id'], result.message_id)

    async def test_failed_fallback_keeps_old_screen(self):
        self.bot.edit_message_media = AsyncMock(side_effect=RuntimeError('offline'))
        self.bot.send_photo = AsyncMock(side_effect=RuntimeError('offline'))
        with self.assertLogs(level='WARNING'):
            await self.click()
        self.assertFalse(any(c['kind'] == 'delete' for c in self.bot.calls))
        self.assertEqual((await self.db.get_user(12))['ui_msg_id'], 100)

    async def test_text_edits_and_cross_type_replacements(self):
        await self.click(None, 'Text menu')
        self.assertEqual([c['kind'] for c in self.bot.calls], ['text', 'delete'])
        self.message = H.FakeMsg((await self.db.get_user(12))['ui_msg_id'], text='Text menu')
        self.message.chat.id = 12
        self.bot.calls.clear()
        await self.click(None, 'Another text menu')
        self.assertEqual(self.bot.calls, [])
        self.assertEqual(self.bot.edits[-1]['kind'], 'text')

    async def test_pin_and_album_are_never_repurposed(self):
        self.db._users[12]['welcome_message_id'] = 100
        self.db._users[12]['welcome_content_hash'] = self.mod.start_here.signature(self.db._users[12])
        with patch.object(self.mod, '_sync_welcome', AsyncMock()) as sync:
            await self.click()
            sync.assert_not_awaited()
        self.assertEqual(self.bot.edits, [])
        self.assertFalse(any(c['kind'] == 'delete' and c['id'] == 100 for c in self.bot.calls))
        current = (await self.db.get_user(12))['ui_msg_id']
        self.message.message_id = current
        self.db._users[12]['album_ids'] = '101,102'
        self.bot.calls.clear()
        await self.click()
        self.assertEqual(self.bot.edits, [])
        self.assertEqual(self.bot.calls[0]['kind'], 'photo')
        self.assertEqual({c['id'] for c in self.bot.calls if c['kind']=='delete'}, {current,101,102})
        self.assertIsNone((await self.db.get_user(12))['album_ids'])

    async def test_foreign_and_stale_callbacks_do_not_edit_current_menu(self):
        self.assertIsNone(N.message_for(13, 100))
        self.message.message_id = 99
        await self.click()
        self.assertEqual(self.bot.edits, [])

    async def test_concurrent_renders_for_one_user_do_not_overlap(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.bot.edit_message_media
        async def slow(**kwargs):
            entered.set()
            await release.wait()
            return await original(**kwargs)
        self.bot.edit_message_media = slow
        first = asyncio.create_task(self.click())
        await entered.wait()
        second = asyncio.create_task(self.click('expiration_time'))
        await asyncio.sleep(0)
        self.assertFalse(second.done())
        self.assertEqual(self.bot.edits, [])
        release.set()
        await asyncio.gather(first, second)
        self.assertEqual(len(self.bot.edits), 2)


if __name__ == '__main__':
    unittest.main()
