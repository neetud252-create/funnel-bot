"""Offline tests. No production credentials, recipients or Telegram requests."""
import asyncio
import os
import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.modules.setdefault('db', types.SimpleNamespace(pool=None))
import verification_reminders as R
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter, TelegramNetworkError
from aiogram.methods import SendMessage


class ReminderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        mode = patch.dict(os.environ, ELENA_ACTIVATION_ONLY='0')
        mode.start()
        self.addCleanup(mode.stop)
        self.conn = types.SimpleNamespace(fetchrow=AsyncMock(return_value={'attempts': 1}),
            execute=AsyncMock(), fetchval=AsyncMock(), fetch=AsyncMock())
        self.bot = types.SimpleNamespace(send_message=AsyncMock())

    async def test_changed_eligibility_or_completed_claim_never_sends(self):
        self.conn.fetchrow.return_value = None
        await R.send_one(self.bot, self.conn, 123)
        self.bot.send_message.assert_not_awaited()

    async def test_send_has_verification_and_optout_controls(self):
        await R.send_one(self.bot, self.conn, 123)
        args = self.bot.send_message.await_args
        self.assertEqual(args.args, (123, R.TEXT))
        self.assertEqual([r[0].callback_data for r in args.kwargs['reply_markup'].inline_keyboard],
                         ['vr:open', 'vr:stop'])
        self.assertEqual(self.conn.execute.await_args.args[1:], (123, 'sent'))

    async def test_forbidden_marks_blocked_for_future_broadcasts(self):
        self.bot.send_message.side_effect = TelegramForbiddenError(method=SendMessage(chat_id=123,text='x'), message='blocked')
        await R.send_one(self.bot, self.conn, 123)
        self.assertEqual(self.conn.execute.await_args_list[0].args[1], 123)
        self.assertIn('broadcast_blocked=TRUE', self.conn.execute.await_args_list[0].args[0])
        self.assertEqual(self.conn.execute.await_args.args[-1], 'blocked')

    async def test_network_ambiguity_not_retried(self):
        self.bot.send_message.side_effect = TelegramNetworkError(method=SendMessage(chat_id=123,text='x'),message='timeout')
        await R.send_one(self.bot, self.conn, 123)
        self.assertEqual(self.bot.send_message.await_count, 1)
        self.assertEqual(self.conn.execute.await_args.args[-1], 'uncertain')

    async def test_rate_limit_waits_and_bounds_retries(self):
        self.bot.send_message.side_effect = TelegramRetryAfter(method=SendMessage(chat_id=123,text='x'),message='wait',retry_after=30)
        self.assertEqual(await R.send_one(self.bot, self.conn, 123), 31)
        self.assertEqual(self.conn.execute.await_args.args[1:], (123, 'pending', 31))
        self.conn.fetchrow.return_value = {'attempts': 5}
        await R.send_one(self.bot, self.conn, 123)
        self.assertEqual(self.conn.execute.await_args.args[1:], (123, 'failed', 31))

    async def test_cancelled_send_leaves_recoverable_sending_marker(self):
        self.bot.send_message.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await R.send_one(self.bot, self.conn, 123)
        self.conn.execute.assert_not_awaited()

    async def test_restart_marks_sending_uncertain_before_select(self):
        self.conn.fetch.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await R.run_queue(self.bot, self.conn)
        self.assertIn("status='uncertain' WHERE status='sending'", self.conn.execute.await_args.args[0])
        self.bot.send_message.assert_not_awaited()

    async def test_admin_controls_private_only_and_no_send(self):
        handlers, callbacks = [], []
        dp = types.SimpleNamespace(message=types.SimpleNamespace(register=lambda h,*f: handlers.append(h)),
            callback_query=types.SimpleNamespace(register=lambda h,*f: callbacks.append(h)))
        open_funnel = AsyncMock()
        R.install(dp, lambda uid: uid == 1, open_funnel)
        m = types.SimpleNamespace(text='/reminderson', chat=types.SimpleNamespace(type='private'),
                                 from_user=types.SimpleNamespace(id=2), answer=AsyncMock())
        pool = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=self.conn)
        with patch.object(R.db, 'pool', pool):
            await handlers[0](m)
            pool.acquire.assert_not_called()
            m.from_user.id = 1
            m.chat.type = 'group'
            await handlers[0](m)
            pool.acquire.assert_not_called()
            m.chat.type = 'private'
            with patch.object(R, 'report', AsyncMock(return_value='Report')):
                await handlers[0](m)
            self.assertEqual(self.conn.execute.await_args.args[-1], True)
            self.bot.send_message.assert_not_awaited()
            cb = types.SimpleNamespace(data='vr:stop',from_user=types.SimpleNamespace(id=2),
                message=types.SimpleNamespace(chat=types.SimpleNamespace(type='private')),answer=AsyncMock())
            state = object()
            await callbacks[0](cb,self.bot,state)
            self.assertEqual(self.conn.execute.await_args.args[-1], 2)
            self.assertIn('broadcast_opt_out=TRUE', self.conn.execute.await_args.args[0])
            cb.data = 'vr:open'
            await callbacks[0](cb,self.bot,state)
            open_funnel.assert_awaited_once_with(cb,self.bot,state)


if __name__ == '__main__':
    unittest.main()
