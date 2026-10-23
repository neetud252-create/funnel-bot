"""Real loader lifecycle with fake Telegram I/O, including navigation races."""
import asyncio
import importlib.util
from pathlib import Path
import types
import unittest
from unittest.mock import AsyncMock, patch

import test_signal_flow as H
import market_progress as progress
import config


class LoadingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = H._install_stub_modules()
        self.active = AsyncMock(return_value=True)
        self.bot = types.SimpleNamespace(send_message=AsyncMock(return_value=types.SimpleNamespace(message_id=102)),
                                         delete_message=AsyncMock(), edit_message_text=AsyncMock())
        async def guarded(tg_id, ui_id, ids):
            user = await self.db.get_user(tg_id)
            if user['ui_msg_id'] != ui_id:
                return False
            await self.db.set_album(tg_id, ids)
            return True
        self.db.set_album_for_ui = AsyncMock(side_effect=guarded)
        async def render(*args, **kwargs):
            await self.db.set_ui_msg(11, 101)
            return types.SimpleNamespace(message_id=101)
        self.render = AsyncMock(side_effect=render)
        self.loader = progress.Loading(self.bot, 11, 'en', self.active)

    async def asyncTearDown(self):
        await self.loader.stop()

    async def test_standalone_emoji_then_short_notice_and_cleanup(self):
        await self.loader.start(self.render)
        self.assertEqual(self.render.await_args.args[2:5], (None, config.SIGNAL_CHART, []))
        self.assertIn('<tg-emoji', self.render.await_args.args[3])
        text = self.bot.send_message.await_args.args[1]
        self.assertIn("I'm analyzing the market.", text)
        self.assertNotIn('30', text)
        self.assertNotIn('Unlock', text)
        self.assertEqual(self.bot.send_message.await_args.kwargs['parse_mode'], 'HTML')
        self.assertEqual((await self.db.get_user(11))['album_ids'], '102')
        await self.loader.stop()
        self.assertEqual([c.kwargs['message_id'] for c in self.bot.delete_message.await_args_list], [101, 102])
        self.assertIsNone((await self.db.get_user(11))['album_ids'])
        await self.loader.stop()
        self.assertEqual(self.bot.delete_message.await_count, 2)

    async def test_navigation_watcher_removes_only_old_messages(self):
        with patch.object(progress, 'POLL_SECONDS', 0.001):
            await self.loader.start(self.render)
            await self.db.set_ui_msg(11, 999)
            await self.db.set_album(11, '1000')
            self.active.return_value = False
            await asyncio.wait_for(self.loader.task, 1)
        self.assertEqual([c.kwargs['message_id'] for c in self.bot.delete_message.await_args_list], [101, 102])
        self.assertEqual((await self.db.get_user(11))['album_ids'], '1000')

    async def test_navigation_during_send_does_not_overwrite_new_screen_companions(self):
        async def send(*a, **kw):
            await self.db.set_ui_msg(11, 999)
            await self.db.set_album(11, '1000')
            self.active.return_value = False
            return types.SimpleNamespace(message_id=102)
        self.bot.send_message.side_effect = send
        await self.loader.start(self.render)
        self.assertIsNone(self.loader.task)
        self.assertEqual((await self.db.get_user(11))['album_ids'], '1000')
        self.assertEqual(self.bot.delete_message.await_count, 2)

    async def test_failed_notice_cleans_emoji_without_blocking_analysis(self):
        self.bot.send_message.side_effect = RuntimeError('Telegram unavailable')
        await self.loader.start(self.render)
        self.assertIsNone(self.loader.task)
        self.bot.delete_message.assert_awaited_once_with(chat_id=11, message_id=101)

    async def test_missing_initial_render_and_inactive_session_are_safe(self):
        self.render.side_effect = None
        self.render.return_value = None
        await self.loader.start(self.render)
        self.bot.send_message.assert_not_awaited()
        self.render.reset_mock()
        self.active.return_value = False
        await self.loader.start(self.render)
        self.render.assert_not_awaited()

    async def test_retry_edits_owned_notice_without_resending_animation(self):
        await self.loader.start(self.render)
        await self.loader.reconnecting()
        self.assertIn(progress.RECONNECTING, self.bot.edit_message_text.await_args.kwargs['text'])
        self.assertEqual(self.bot.edit_message_text.await_args.kwargs['message_id'], 102)
        self.bot.send_message.assert_awaited_once()
        self.active.return_value = False
        await self.loader.reconnecting()
        self.bot.edit_message_text.assert_awaited_once()

    async def test_database_update_is_guarded_by_ui_owner(self):
        spec = importlib.util.spec_from_file_location('real_db_for_progress_test', Path(__file__).with_name('db.py'))
        real_db = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(real_db)
        connection = types.SimpleNamespace(execute=AsyncMock(return_value='UPDATE 1'))
        context = unittest.mock.MagicMock()
        context.__aenter__ = AsyncMock(return_value=connection)
        context.__aexit__ = AsyncMock(return_value=False)
        real_db.pool = types.SimpleNamespace(acquire=lambda: context)
        self.assertTrue(await real_db.set_album_for_ui(11, 101, '102'))
        query, *params = connection.execute.await_args.args
        self.assertIn('WHERE tg_id=$2 AND ui_msg_id=$3', query)
        self.assertEqual(params, ['102', 11, 101])
        connection.execute.return_value = 'UPDATE 0'
        self.assertFalse(await real_db.set_album_for_ui(11, 101, None))


if __name__ == '__main__':
    unittest.main()
