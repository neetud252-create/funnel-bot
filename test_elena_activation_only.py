"""Activation-only routing and delivery barriers, with no real customer messages."""
import asyncio
import os
import types
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault('DATABASE_URL', 'postgresql://unused')
import elena as E
import verification_reminders as R
from test_elena import CONNECTION, session, user


class ActivationOnlyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        mode = patch.dict(os.environ, ELENA_ENABLED='1', ELENA_ACTIVATION_ENABLED='1',
                          ELENA_ACTIVATION_ONLY='1')
        mode.start()
        self.addCleanup(mode.stop)
        self.bot = types.SimpleNamespace(send_message=AsyncMock(), send_voice=AsyncMock())

    def message(self, text='hi'):
        return types.SimpleNamespace(chat=types.SimpleNamespace(id=42, type='private'),
            from_user=types.SimpleNamespace(id=42), message_id=12, text=text,
            business_connection_id=CONNECTION['connection_id'], sender_business_bot=None,
            via_bot=None, is_from_offline=False)

    def context(self, current):
        stack = ExitStack()
        for target, name, value in (
            (E.store, 'connection', CONNECTION), (E.store, 'session', current),
            (E.store, 'incoming', current), (E.db, 'get_user', user()),
            (E, 'active_connection', CONNECTION),
        ):
            stack.enter_context(patch.object(target, name, AsyncMock(return_value=value)))
        return stack

    def test_mode_defaults_to_activation_only(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(E.activation_only())
        with patch.dict(os.environ, ELENA_ACTIVATION_ONLY='0'):
            self.assertFalse(E.activation_only())

    async def test_first_message_only_confirms_activation_and_opens_bot(self):
        pending = session(activated_at=None, activation_version=0)
        with self.context(pending), \
             patch.object(E.store, 'activate', AsyncMock(return_value=session())) as activate, \
             patch.object(E.activation, 'deliver', AsyncMock()) as deliver, \
             patch.object(E, 'ai_answer', AsyncMock()) as ai, \
             patch.object(E, 'send_chat_reply', AsyncMock()) as chat:
            await E.business_message(self.message('how do I register?'), self.bot)
        activate.assert_awaited_once_with(42, CONNECTION['connection_id'], 12, 'ready', 0)
        deliver.assert_awaited_once_with(self.bot, 42)
        self.bot.send_message.assert_awaited_once()
        self.assertEqual(self.bot.send_message.await_args.args, (42, E.copy.ACTIVATED))
        self.assertEqual(self.bot.send_message.await_args.kwargs['business_connection_id'],
                         CONNECTION['connection_id'])
        ai.assert_not_awaited()
        chat.assert_not_awaited()

    async def test_activated_chats_and_commands_stay_silent(self):
        with self.context(session()), \
             patch.object(E.store, 'activate', AsyncMock()) as activate, \
             patch.object(E.store, 'resume', AsyncMock()) as resume, \
             patch.object(E, 'ai_answer', AsyncMock()) as ai, \
             patch.object(E, 'send_chat_reply', AsyncMock()) as chat, \
             patch.object(E, 'voice_reply', AsyncMock()) as voice:
            for text in ('hi', 'wrong UID', 'I deposited', '/resume', '/ai', '/basic',
                         '/voice', '/privacy', E.activation.DRAFT, None):
                await E.business_message(self.message(text), self.bot)
        for mock in (activate, resume, ai, chat, voice, self.bot.send_message, self.bot.send_voice):
            mock.assert_not_awaited()

    async def test_duplicate_and_unrelated_messages_do_not_activate(self):
        with self.context(session(activated_at=None)), \
             patch.object(E.store, 'incoming', AsyncMock(return_value=None)), \
             patch.object(E.store, 'activate', AsyncMock()) as activate:
            await E.business_message(self.message(), self.bot)
            foreign = self.message()
            foreign.business_connection_id = 'other-account'
            await E.business_message(foreign, self.bot)
            with patch.object(E.db, 'get_user', AsyncMock(return_value=None)):
                await E.business_message(self.message(), self.bot)
        activate.assert_not_awaited()
        self.bot.send_message.assert_not_awaited()

    async def test_stop_keeps_opt_out_without_reply(self):
        with self.context(session()), \
             patch.object(E.store, 'pause', AsyncMock()) as pause, \
             patch.object(E.db, 'set_notifications', AsyncMock()) as notifications:
            await E.business_message(self.message('stop'), self.bot)
        pause.assert_awaited_once_with(42, stopped=True, handoff=False)
        notifications.assert_awaited_once_with(42, False)
        self.bot.send_message.assert_not_awaited()

    async def test_old_help_buttons_cannot_restart_chat_or_voice(self):
        cb = types.SimpleNamespace(from_user=types.SimpleNamespace(id=42),
            message=self.message(), answer=AsyncMock(), data='')
        with self.context(session()), \
             patch.object(E, 'ai_answer', AsyncMock()) as ai, \
             patch.object(E, 'voice_reply', AsyncMock()) as voice, \
             patch.object(E, 'refresh', AsyncMock()) as refresh, \
             patch.object(E.store, 'consent', AsyncMock()) as consent, \
             patch.object(E.store, 'resume', AsyncMock()) as resume:
            for action in ('account', 'uid', 'recheck', 'consent', 'local', 'resume', 'voice:old'):
                cb.data = 'elena:' + action
                await E.callback(cb, self.bot)
        self.assertEqual(cb.answer.await_count, 7)
        for mock in (ai, voice, refresh, consent, resume, self.bot.send_message, self.bot.send_voice):
            mock.assert_not_awaited()

    async def test_direct_reply_and_provider_paths_are_blocked(self):
        with patch.object(E.ai, 'reply', AsyncMock()) as ai, \
             patch.object(E.ai, 'speech', AsyncMock()) as speech, \
             patch.object(E.store, 'claim_ai', AsyncMock()) as claim_ai, \
             patch.object(E.store, 'claim_voice', AsyncMock()) as claim_voice:
            self.assertFalse(await E.send_business(self.bot, 42, 'old reply'))
            await E.ai_answer(self.bot, 42, user(), 'hi')
            await E.voice_reply(self.bot, 42, 'old', 'en')
        for mock in (ai, speech, claim_ai, claim_voice, self.bot.send_message, self.bot.send_voice):
            mock.assert_not_awaited()

    async def test_old_scheduled_jobs_and_verification_retries_never_send(self):
        conn = types.SimpleNamespace(fetchrow=AsyncMock(), execute=AsyncMock(), fetch=AsyncMock())
        for reason in ('uid', 'deposit', 'inactive', 'registration', 'problem'):
            await E.send_job(self.bot, conn, session(reason=reason))
        self.assertEqual(await R.send_one(self.bot, conn, 42), 0)
        await R.run_queue(self.bot, conn)
        await R.worker(self.bot)
        conn.fetchrow.assert_not_awaited()
        conn.fetch.assert_not_awaited()
        self.bot.send_message.assert_not_awaited()

    async def test_normal_bot_activity_still_works_without_scheduling(self):
        event = self.message('/start')
        event.from_user.is_bot = False
        event.business_connection_id = None
        handler = AsyncMock(return_value='Pocket IQ menu')
        with patch.object(E.store, 'activity', AsyncMock()) as activity, \
             patch.object(E.store, 'observe', AsyncMock()) as observe:
            self.assertEqual(await E.ActivityMiddleware()(handler, event, {}), 'Pocket IQ menu')
        handler.assert_awaited_once_with(event, {})
        activity.assert_not_awaited()
        observe.assert_not_awaited()

    async def test_worker_retries_menu_handoff_but_does_not_load_reminders(self):
        conn = types.SimpleNamespace(fetchval=AsyncMock(return_value=True), execute=AsyncMock())
        pool = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        with patch.object(E.db, 'pool', pool), \
             patch.object(E.store, 'pending_activations', AsyncMock(return_value=[{'tg_id':42}])), \
             patch.object(E.activation, 'deliver', AsyncMock()) as deliver, \
             patch.object(E.store, 'due', AsyncMock()) as due, \
             patch.object(E.asyncio, 'sleep', AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError):
                await E.worker(self.bot)
        deliver.assert_awaited_once_with(self.bot, 42)
        due.assert_not_awaited()
        self.bot.send_message.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
