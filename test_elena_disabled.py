"""Manual support must stay silent even with old sessions, callbacks and jobs."""
import asyncio
import os
import types
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault('DATABASE_URL', 'postgresql://unused')
import elena as E
import verification_reminders as R


class ManualSupportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        # The master off switch must win over every previously enabled option.
        self.stack.enter_context(patch.dict(os.environ, ELENA_ENABLED='0',
            ELENA_ACTIVATION_ENABLED='1', ELENA_ACTIVATION_ONLY='0', ELENA_STARTUP_CHECK='1'))
        self.pool = self.stack.enter_context(patch.object(E.db, 'pool', MagicMock()))
        self.mocks = []
        for target, names in ((E.store, ('session', 'connection', 'observe', 'claim',
                'claim_activation', 'claim_voice', 'consent', 'resume', 'save_connection')),
                (E.ai, ('reply', 'speech'))):
            for name in names:
                self.mocks.append(self.stack.enter_context(patch.object(target, name, AsyncMock())))
        self.bot = types.SimpleNamespace(send_message=AsyncMock(), send_voice=AsyncMock(),
            send_photo=AsyncMock(), send_chat_action=AsyncMock(), get_business_connection=AsyncMock())

    def assert_quiet(self):
        self.pool.acquire.assert_not_called()
        for mock in self.mocks + list(vars(self.bot).values()):
            mock.assert_not_awaited()

    def test_automation_and_gate_default_off(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(E.enabled())
            self.assertFalse(E.activation.enabled())
        with patch.dict(os.environ, ELENA_ENABLED='1'):
            os.environ.pop('ELENA_ACTIVATION_ENABLED', None)
            self.assertFalse(E.activation.enabled())

    async def test_incoming_messages_old_buttons_and_connections_are_silent(self):
        for value in ('hi', E.activation.DRAFT, '/resume', '/voice', '/ai', 'wrong UID'):
            await E.business_message(types.SimpleNamespace(text=value), self.bot)
        for action in ('consent', 'resume', 'voice:old', 'account', 'recheck', 'stop', 'human'):
            cb = types.SimpleNamespace(data='elena:' + action, answer=AsyncMock())
            await E.callback(cb, self.bot)
            cb.answer.assert_awaited_once_with()
        await E.connection_changed(object())
        await E.open_elena(object(), self.bot)
        self.assert_quiet()

    async def test_direct_reply_and_activation_paths_cannot_call_ai_or_send(self):
        await E.ai_answer(self.bot, 42, {}, 'hi')
        await E.voice_reply(self.bot, 42, 'old', 'en')
        await E.allow_ai(self.bot, 42, {})
        await E.resume_chat(self.bot, 42, {}, {})
        self.assertFalse(await E.send_chat_reply(self.bot, 42, 'hello', object()))
        self.assertFalse(await E.send_business(self.bot, 42, 'activation', activation_confirmation=True))
        self.assertFalse(await E.send_proof(self.bot, 42))
        self.assertIsNone(await E.active_connection(self.bot))
        self.assertFalse(await E.activation.required(42))
        await E.activation.deliver(self.bot, 42)
        self.assert_quiet()

    async def test_queued_work_and_startup_probe_never_run(self):
        await E.send_job(self.bot, object(), {'tg_id': 42, 'status': 'pending'})
        await asyncio.wait_for(E.worker(self.bot), .25)
        await R.run_queue(self.bot, object())
        self.assertEqual(await R.send_one(self.bot, object(), 42), 0)
        await R.worker(self.bot)
        self.assertIn('manual support', await R.report(object()))
        self.assert_quiet()

    async def test_activity_still_runs_normal_handler_without_scheduling(self):
        handler = AsyncMock(return_value='normal bot response')
        result = await E.ActivityMiddleware()(handler, object(), {})
        self.assertEqual(result, 'normal bot response')
        handler.assert_awaited_once()
        self.assert_quiet()

    async def test_admin_reminder_command_cannot_reenable_automation(self):
        dp = types.SimpleNamespace(message=MagicMock(), callback_query=MagicMock())
        R.install(dp, lambda uid: True, AsyncMock())
        command = dp.message.register.call_args.args[0]
        message = types.SimpleNamespace(text='/reminderson',
            chat=types.SimpleNamespace(type='private'), from_user=types.SimpleNamespace(id=42), answer=AsyncMock())
        await command(message)
        self.assertIn('OFF', message.answer.await_args.args[0])
        with patch.object(E.config, 'ADMIN_IDS', [42]):
            await E.status_command(message)
        self.assertIn('manual replies', message.answer.await_args.args[0])
        self.assert_quiet()


if __name__ == '__main__':
    unittest.main()
