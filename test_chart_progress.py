"""Loading edits must not outlive their session or affect analysis delivery."""
import asyncio
import types
import unittest
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import EditMessageText
import chart_progress as ui
import localization


class ProgressTests(unittest.IsolatedAsyncioTestCase):
    def make(self, edit=None, active=None):
        bot = types.SimpleNamespace(edit_message_text=edit or AsyncMock())
        message = types.SimpleNamespace(message_id=22, reply_markup=None)
        return ui.Progress(bot, 11, message, 'en', active or AsyncMock(return_value=True))

    async def test_edits_one_message_and_stops_before_result(self):
        called = asyncio.Event()
        async def edit(**kwargs):
            called.set()
        progress = self.make(AsyncMock(side_effect=edit))
        with patch.object(ui, 'FRAME_SECONDS', 0.001):
            progress.start()
            await asyncio.wait_for(called.wait(), 1)
            await progress.stop()
        calls = progress.bot.edit_message_text.await_count
        await asyncio.sleep(0.01)
        self.assertEqual(progress.bot.edit_message_text.await_count, calls)
        call = progress.bot.edit_message_text.await_args.kwargs
        self.assertEqual((call['chat_id'], call['message_id']), (11, 22))
        self.assertNotIn('Analysis complete', call['text'])
        self.assertNotIn('%', call['text'])
        self.assertIsNone(progress.task)

    async def test_navigation_and_failed_initial_render_do_not_edit(self):
        progress = self.make(active=AsyncMock(return_value=False))
        with patch.object(ui, 'FRAME_SECONDS', 0):
            progress.start()
            await progress.task
        progress.bot.edit_message_text.assert_not_awaited()
        missing = ui.Progress(progress.bot, 11, None, 'en', progress.active)
        missing.start()
        self.assertIsNone(missing.task)

    async def test_edit_failure_is_best_effort(self):
        progress = self.make(AsyncMock(side_effect=RuntimeError('edit rejected')))
        with patch.object(ui, 'FRAME_SECONDS', 0):
            progress.start()
            await progress.task
            await progress.stop()
        progress.bot.edit_message_text.assert_awaited_once()

    async def test_rate_limit_does_not_retry_early(self):
        called = asyncio.Event()
        async def edit(**kwargs):
            called.set()
            raise TelegramRetryAfter(method=EditMessageText(chat_id=11, message_id=22, text='x'),
                                     message='rate limit', retry_after=2)
        progress = self.make(AsyncMock(side_effect=edit))
        with patch.object(ui, 'FRAME_SECONDS', 0.001):
            progress.start()
            await asyncio.wait_for(called.wait(), 1)
            await asyncio.sleep(0.02)
            await progress.stop()
        progress.bot.edit_message_text.assert_awaited_once()

    async def test_stop_cancels_pending_edit(self):
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def edit(**kwargs):
            started.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()
        progress = self.make(AsyncMock(side_effect=edit))
        with patch.object(ui, 'FRAME_SECONDS', 0):
            progress.start()
            await asyncio.wait_for(started.wait(), 1)
            await progress.stop()
        self.assertTrue(cancelled.is_set())

    def test_retries_and_completion_are_localized(self):
        for language in localization.CODES:
            loading = ui.frame(language, retrying=True)
            complete = ui.completed(language)
            if language != 'en':
                self.assertNotIn('Reconnecting to AI', loading)
                self.assertNotIn('Analysis complete', complete)
            self.assertNotIn('▰▰▰▰▰▰▰▰▰▰', loading)
            self.assertIn('▰▰▰▰▰▰▰▰▰▰', complete)


if __name__ == '__main__':
    unittest.main()
