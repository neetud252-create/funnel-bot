"""Offline broadcast authorization, preview, copy and queue regression checks."""
import asyncio
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

# No production connection or credentials are used by these tests.
sys.modules['db'] = types.SimpleNamespace(pool=None)
import broadcast as B
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter, TelegramNetworkError
from aiogram.methods import CopyMessage


class Registry:
    def __init__(self):
        self.handlers = []

    def register(self, handler, *filters):
        self.handlers.append(handler)


def post(**changes):
    value = dict(id='abcdef123456', admin_id=1, source_chat=1, source_message=5,
                 video_note=False, audience='all', button_kind='none', status='draft')
    value.update(changes)
    return value


class BroadcastTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dp = types.SimpleNamespace(message=Registry(), callback_query=Registry())
        self.open = AsyncMock()
        B.install(self.dp, lambda uid: uid == 1, self.open)
        self.callback = self.dp.callback_query.handlers[0]
        self.bot = types.SimpleNamespace(copy_message=AsyncMock(), send_message=AsyncMock())
        self.cb = types.SimpleNamespace(data='bc:preview:abcdef123456',
            from_user=types.SimpleNamespace(id=1), answer=AsyncMock(),
            message=types.SimpleNamespace(edit_reply_markup=AsyncMock()))

    async def test_non_admin_cannot_draft_or_send(self):
        m = types.SimpleNamespace(chat=types.SimpleNamespace(type='private'),
                                 from_user=types.SimpleNamespace(id=22), answer=AsyncMock())
        with patch.object(B, 'query', AsyncMock()) as query:
            await self.dp.message.handlers[0](m)
            query.assert_not_awaited()
        self.cb.from_user.id = 22
        with patch.object(B, 'draft', AsyncMock()) as draft:
            await self.callback(self.cb, self.bot, object())
            draft.assert_not_awaited()

    async def test_preview_only_copies_to_admin(self):
        with patch.object(B, 'draft', AsyncMock(return_value=post())), \
             patch.object(B, 'count_recipients', AsyncMock(return_value=20)), \
             patch.object(B, 'enqueue', AsyncMock()) as enqueue:
            await self.callback(self.cb, self.bot, object())
            self.assertEqual(self.bot.copy_message.await_args.args, (1, 1, 5))
            enqueue.assert_not_awaited()
            self.assertIn('PREVIEW ONLY', self.bot.send_message.await_args.args[1])

    async def test_stale_preview_and_repeat_confirmation(self):
        signature = B.preview_signature(post())
        self.cb.data = f'bc:send:abcdef123456:{signature}'
        with patch.object(B, 'draft', AsyncMock(return_value=post(source_message=6))), \
             patch.object(B, 'enqueue', AsyncMock()) as enqueue:
            await self.callback(self.cb, self.bot, object())
            enqueue.assert_not_awaited()
        with patch.object(B, 'draft', AsyncMock(return_value=None)), \
             patch.object(B, 'enqueue', AsyncMock()) as enqueue:
            await self.callback(self.cb, self.bot, object())
            enqueue.assert_not_awaited()

    async def test_media_copy_and_round_video_controls(self):
        await B.deliver(self.bot, post(button_kind='register'), 9)
        markup = self.bot.copy_message.await_args.kwargs['reply_markup']
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, 'bc:open')
        self.assertEqual(markup.inline_keyboard[-1][0].callback_data, 'bc:stop')
        await B.deliver(self.bot, post(video_note=True), 10)
        self.assertEqual(self.bot.copy_message.await_args.args, (10, 1, 5))
        self.assertEqual(self.bot.send_message.await_args.args[0], 10)

    async def test_enqueue_claims_draft_once_and_snapshots_recipients(self):
        # MagicMock supplies the asynchronous context manager protocol.
        from unittest.mock import MagicMock
        tx = MagicMock()
        tx.__aenter__ = AsyncMock()
        tx.__aexit__ = AsyncMock(return_value=False)
        conn = types.SimpleNamespace(transaction=lambda: tx,
            fetchrow=AsyncMock(side_effect=[{'id': 'abcdef123456'}, None]), execute=AsyncMock())
        acquire = MagicMock()
        acquire.__aenter__ = AsyncMock(return_value=conn)
        acquire.__aexit__ = AsyncMock(return_value=False)
        with patch.object(B.db, 'pool', types.SimpleNamespace(acquire=lambda: acquire)):
            self.assertTrue(await B.enqueue(post(audience='active')))
            self.assertFalse(await B.enqueue(post(audience='active')))
        conn.execute.assert_awaited_once()
        self.assertIn('verified=TRUE', conn.execute.await_args.args[0])
        self.assertIn('NOT broadcast_opt_out', conn.execute.await_args.args[0])
        self.assertIn("status='draft'", conn.fetchrow.await_args.args[0])

    async def test_open_button_uses_existing_funnel_callback(self):
        self.cb.data = 'bc:open'
        state = object()
        await self.callback(self.cb, self.bot, state)
        self.open.assert_awaited_once_with(self.cb, self.bot, state)

    async def test_audience_filters_and_opt_out(self):
        with patch.object(B, 'query', AsyncMock(return_value=[{'n': 4}])) as query:
            for audience in B.AUDIENCES:
                self.assertEqual(await B.count_recipients(post(audience=audience)), 4)
                sql = query.await_args.args[0]
                self.assertIn('NOT broadcast_opt_out', sql)
                self.assertIn('NOT broadcast_blocked', sql)
        self.cb.data = 'bc:stop'
        self.cb.from_user.id = 9
        with patch.object(B, 'query', AsyncMock()) as query:
            await self.callback(self.cb, self.bot, object())
            self.assertEqual(query.await_args.args[1], 9)
        self.open.assert_not_awaited()

    async def test_queue_retries_blocked_opt_out_and_ambiguous_delivery(self):
        method = CopyMessage(chat_id=9, from_chat_id=1, message_id=5)
        conn = types.SimpleNamespace(execute=AsyncMock(), fetch=AsyncMock(side_effect=[
            [{'tg_id': n} for n in (10, 11, 12, 13)], []]),
            fetchrow=AsyncMock(side_effect=[post(status='queued'),
                {'broadcast_opt_out': False, 'broadcast_blocked': False},
                {'broadcast_opt_out': False, 'broadcast_blocked': False},
                {'broadcast_opt_out': True, 'broadcast_blocked': False},
                {'broadcast_opt_out': False, 'broadcast_blocked': False},
                post(status='running'), asyncio.CancelledError()]))
        delivery = AsyncMock(side_effect=[TelegramRetryAfter(method=method,message='slow',retry_after=1),
            None, TelegramForbiddenError(method=method,message='blocked'),
            TelegramNetworkError(method=method,message='timeout')])
        with patch.object(B, 'deliver', delivery), \
             patch.object(B, 'report', AsyncMock(return_value='done')), \
             patch.object(B.asyncio, 'sleep', AsyncMock()) as sleep:
            with self.assertRaises(asyncio.CancelledError):
                await B.run_queue(self.bot, conn)
            self.assertEqual(delivery.await_count, 4)
            results = [c.args[3] for c in conn.execute.await_args_list
                       if len(c.args) == 4 and 'SET status=$3' in c.args[0]]
            self.assertEqual(results, ['sent', 'blocked', 'skipped', 'uncertain'])
            self.assertIn('status=\'uncertain\'', conn.execute.await_args_list[0].args[0])
            self.assertTrue(any(c.args == (2,) for c in sleep.await_args_list))


if __name__ == '__main__':
    unittest.main()
