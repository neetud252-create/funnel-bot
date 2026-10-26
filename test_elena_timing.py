"""Pacing and deadlines; virtual elapsed time plus short real cancellation tests."""
import asyncio
import os
import time
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch
os.environ.setdefault('DATABASE_URL', 'postgresql://unused')
import elena as E
import elena_timing as T
from test_elena import session, user


class Clock:
    def __init__(self):
        self.now = 100.0
        self.waits = []

    async def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


class TimingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        mode = patch.dict(os.environ, ELENA_ACTIVATION_ONLY='0')
        mode.start()
        self.addCleanup(mode.stop)

    def model_context(self, model, send, row=None):
        row = row or session(consent=False, history=[])
        stack = ExitStack()
        for obj, name, mock in (
            (E.store, 'claim_ai', AsyncMock(return_value=row)),
            (E.store, 'session', AsyncMock(return_value=row)),
            (E.store, 'remember', AsyncMock(return_value={'tg_id':42})),
            (E.ai, 'reply', model),
            (E, 'send_business', send),
        ):
            stack.enter_context(patch.object(obj, name, mock))
        return stack

    async def test_fast_model_and_padding_total_four_five_or_six_seconds(self):
        for delay in (4.0, 5.0, 6.0):
            clock = Clock()
            delivered = []
            async def model(*args, **kwargs):
                clock.now += 2.0
                return {'reply': 'Hi! How can I help you?', 'action':'none'}
            async def send(*args):
                delivered.append(clock.now - 100.0)
                return True
            with self.subTest(delay=delay), \
                 patch.object(T, 'monotonic', lambda: clock.now), \
                 patch.object(T.random, 'choice', return_value=delay) as choose, \
                 patch.object(T.asyncio, 'sleep', clock.sleep), \
                 self.model_context(AsyncMock(side_effect=model), AsyncMock(side_effect=send)):
                await E.ai_answer(object(), 42, user(), 'hi')
            choose.assert_called_once_with((4.0, 5.0, 6.0))
            self.assertEqual(delivered, [delay])
            self.assertEqual(clock.waits, [delay - 2.0])

    async def test_queue_and_lookup_time_are_not_followed_by_another_full_delay(self):
        clock = Clock()
        clock.now = 105.5
        timing = T.ReplyTiming(100.0, 6.0)
        with patch.object(T, 'monotonic', lambda: clock.now), \
             patch.object(T.asyncio, 'sleep', clock.sleep):
            self.assertEqual(timing.processing_remaining(), .5)
            await timing.wait()
            self.assertEqual(clock.now, 106.0)
            clock.now = 106.5
            await timing.wait()
            self.assertEqual(timing.remaining(), 1.5)
        self.assertEqual(clock.waits, [.5])

    async def test_slow_model_is_cancelled_and_gets_one_relevant_fallback(self):
        cancelled = []
        async def slow_model(*args, **kwargs):
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.append(True)
        send = AsyncMock(return_value=True)
        with patch.object(T, 'REPLY_BUDGET', .4), \
             patch.object(T, 'DELIVERY_RESERVE', .2), \
             self.model_context(AsyncMock(side_effect=slow_model), send):
            started = time.monotonic()
            await E.ai_answer(object(), 42, user(), 'how do I register',
                              timing=T.ReplyTiming(started, .04))
        self.assertLess(time.monotonic() - started, .8)
        self.assertEqual(cancelled, [True])
        send.assert_awaited_once()
        self.assertIn(E.copy.CHAT_REGISTER, send.await_args.args[2])

    async def test_model_capacity_queue_is_included_in_deadline(self):
        real_reply = E.ai.reply
        send = AsyncMock(return_value=True)
        with patch.object(T, 'REPLY_BUDGET', .4), \
             patch.object(T, 'DELIVERY_RESERVE', .2), \
             patch.dict(os.environ, {'OPENAI_API_KEY': 'test-only'}), \
             patch.object(E.ai, 'LIMIT', asyncio.Semaphore(0)), \
             patch.object(E.ai.httpx, 'AsyncClient') as client, \
             self.model_context(real_reply, send):
            await E.ai_answer(object(), 42, user(), 'hi',
                              timing=T.ReplyTiming(time.monotonic(), .04))
        client.assert_not_called()
        send.assert_awaited_once()

    async def test_stop_handoff_or_changed_revision_during_pause_suppresses_reply(self):
        for changed in ({'stopped':True}, {'handoff':True}, {'revision':2}, {'local_only':True}):
            clock = Clock()
            row = session(revision=1)
            async def pause(seconds):
                await clock.sleep(seconds)
                row.update(changed)
            with self.subTest(changed=changed), \
                 patch.object(T, 'monotonic', lambda: clock.now), \
                 patch.object(T.asyncio, 'sleep', pause), \
                 patch.object(E.store, 'session', AsyncMock(return_value=row)), \
                 patch.object(E, 'send_business', AsyncMock()) as send:
                sent = await E.send_chat_reply(object(), 42, 'Hi', T.ReplyTiming(100, 5),
                                               revision=1, requires_ai=True)
            self.assertFalse(sent)
            send.assert_not_awaited()

    async def test_local_help_uses_same_pacing_without_enabling_ai(self):
        clock = Clock()
        with patch.object(T, 'monotonic', lambda: clock.now), \
             patch.object(T.asyncio, 'sleep', clock.sleep), \
             patch.object(E.store, 'session', AsyncMock(return_value=session(local_only=True))), \
             patch.object(E, 'send_business', AsyncMock(return_value=True)) as send:
            await E.send_chat_reply(object(), 42, E.copy.HELLO, T.ReplyTiming(100, 4), revision=1)
        self.assertEqual(clock.now, 104)
        send.assert_awaited_once()

    async def test_slow_telegram_send_is_cancelled_without_duplicate_retry(self):
        send = AsyncMock(side_effect=lambda *args: None)
        async def blocked_send(*args):
            await asyncio.sleep(10)
        send.side_effect = blocked_send
        with patch.object(T, 'REPLY_BUDGET', .4), \
             patch.object(T, 'DELIVERY_RESERVE', .2), \
             patch.object(E.store, 'session', AsyncMock(return_value=session())), \
             patch.object(E, 'send_business', send):
            started = time.monotonic()
            sent = await E.send_chat_reply(object(), 42, 'Hi', T.ReplyTiming(started, .04), revision=1)
        self.assertFalse(sent)
        send.assert_awaited_once()
        self.assertLess(time.monotonic() - started, .8)


if __name__ == '__main__':
    unittest.main()
