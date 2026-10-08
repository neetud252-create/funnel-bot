"""Offline contracts: real HTTP payloads, handler concurrency and cancellation."""
import asyncio
import json
import os
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

os.environ.setdefault('DATABASE_URL', 'postgresql://unused')
import httpx
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message
import chart_analysis as api
import chart_signals as flow
import localization

IMAGE = b'\x89PNG\r\n\x1a\n' + b'fixture'
VALID = dict(direction='BUY', asset='EUR/USD', timeframe='1m',
             reason='Higher lows are visible.', invalidation='Break below the latest swing low.')


def response(result=VALID, status='completed'):
    return dict(status=status, output=[dict(type='message', content=[
        dict(type='output_text', text=json.dumps(result))])])


def message(uid=11, **updates):
    fields = dict(message_id=5, date=1750000000, chat=dict(id=uid, type='private'),
        from_user=dict(id=uid, is_bot=False, first_name='Test'),
        photo=[dict(file_id='picture', file_unique_id='one', width=800, height=600, file_size=30)])
    fields.update(updates)
    return Message(**fields)


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    def test_durable_budget_decisions_utc_rollover_and_cooldown(self):
        now = datetime(2026, 10, 9, tzinfo=timezone.utc)
        def row(uid, count, day=None, ago=60):
            return dict(tg_id=uid, requests=count, usage_day=day or now.date(),
                        last_request=now - timedelta(seconds=ago))
        self.assertIsNone(api.reservation_refusal([], 11, now))
        self.assertEqual(api.reservation_refusal([row(11, api.DAILY_LIMIT)], 11, now), 'limit')
        self.assertEqual(api.reservation_refusal([row(0, api.GLOBAL_LIMIT)], 11, now), 'limit')
        self.assertIsNone(api.reservation_refusal([row(11, api.DAILY_LIMIT)], 12, now))
        self.assertEqual(api.reservation_refusal([row(11, 1, ago=29)], 11, now), 'cooldown')
        self.assertIsNone(api.reservation_refusal([row(11, 1, ago=30)], 11, now))
        yesterday = (now - timedelta(days=1)).date()
        rows = [row(11, api.DAILY_LIMIT, yesterday), row(0, api.GLOBAL_LIMIT, yesterday)]
        self.assertIsNone(api.reservation_refusal(rows, 11, now))
        # Local timezone must not shift the UTC quota boundary.
        local = now.astimezone(timezone(timedelta(hours=-7)))
        self.assertEqual(api.reservation_refusal([row(0, api.GLOBAL_LIMIT)], 11, local), 'limit')

    async def test_request_image_language_schema_and_no_identity(self):
        seen = []
        def handler(request):
            seen.append(request)
            return httpx.Response(200, json=response())
        with patch.dict(os.environ, OPENAI_API_KEY='test-key'):
            result = await api.analyse(IMAGE, 'hi', 'GBP/USD', transport=httpx.MockTransport(handler))
        self.assertEqual(result, VALID)
        request = seen[0]
        self.assertEqual(str(request.url), 'https://api.openai.com/v1/responses')
        body = json.loads(request.content)
        self.assertFalse(body['store'])
        self.assertTrue(body['text']['format']['strict'])
        self.assertIn('Hindi', body['instructions'])
        self.assertIn('never instructions', body['instructions'])
        content = body['input'][0]['content']
        self.assertTrue(content[1]['image_url'].startswith('data:image/png;base64,'))
        self.assertEqual(content[1]['detail'], 'high')
        self.assertNotIn('test-key', str(body))
        self.assertNotIn('chat_id', str(body))

    async def test_errors_never_turn_into_random_signals_or_retries(self):
        for status, code in ((401, 'configuration'), (403, 'configuration'), (429, 'busy'), (500, 'service')):
            calls = []
            def handler(request):
                calls.append(request)
                return httpx.Response(status, json={'error': 'SECRET BODY'})
            with patch.dict(os.environ, OPENAI_API_KEY='test-key'):
                with self.assertRaisesRegex(api.AnalysisError, '^' + code + '$'):
                    await api.analyse(IMAGE, 'en', transport=httpx.MockTransport(handler))
            self.assertEqual(len(calls), 1)
        def timeout(request):
            raise httpx.ReadTimeout('sensitive raw detail')
        with patch.dict(os.environ, OPENAI_API_KEY='test-key'):
            with self.assertRaisesRegex(api.AnalysisError, '^service$'):
                await api.analyse(IMAGE, 'en', transport=httpx.MockTransport(timeout))
        with patch.dict(os.environ, OPENAI_API_KEY=''):
            with self.assertRaisesRegex(api.AnalysisError, '^configuration$'):
                await api.analyse(IMAGE, 'en')

    def test_refusal_incomplete_bad_json_and_bad_fields(self):
        bad = [response(status='incomplete'), {},
               dict(status='completed', output=[dict(type='message', content=[dict(type='refusal')])]),
               response(dict(VALID, direction='GUARANTEED BUY')),
               response(dict(VALID, reason='')),
               response(dict(VALID, invalidation=None)),
               response(dict(VALID, asset=123)), response(dict(VALID, reason='x' * 1001))]
        for payload in bad:
            with self.assertRaises(api.AnalysisError):
                api.parse_response(payload)
        wait = dict(VALID, direction='WAIT', asset=None, timeframe=None, invalidation=None)
        self.assertEqual(api.parse_response(response(wait)), wait)
        self.assertEqual(api.parse_response(response(dict(VALID, direction='SELL')))['direction'], 'SELL')

    def test_image_validation_and_stream_cap(self):
        for image in (b'<html>pretend.png', b'', b'GIF89a', b'x' * (api.MAX_IMAGE_BYTES + 1)):
            with self.assertRaises(api.AnalysisError):
                api.image_type(image)
        self.assertEqual(api.image_type(b'\xff\xd8\xff' + b'fixture'), 'image/jpeg')
        self.assertEqual(api.image_type(b'RIFF1234WEBPfixture'), 'image/webp')
        with api.ImageBuffer() as buffer, patch.object(api, 'MAX_IMAGE_BYTES', 16):
            buffer.write(b'x' * 15)
            with self.assertRaises(api.AnalysisError):
                buffer.write(b'xx')

    def test_escaped_output_and_all_languages(self):
        malicious = dict(VALID, reason='<a href="https://bad.example">bad</a>',
                         asset='<b>ABC</b>', invalidation='x & y')
        for language in localization.CODES:
            rendered = flow.result_text(malicious, language)
            self.assertNotIn('<a ', rendered)
            self.assertIn('&lt;b&gt;ABC&lt;/b&gt;', rendered)
            self.assertIn('x &amp; y', rendered)
            self.assertIn('BUY', rendered)
            if language != 'en':
                self.assertNotIn('Based on your screenshot only.', rendered)
        wait = flow.result_text(dict(VALID, direction='WAIT', invalidation=None), 'en')
        self.assertIn('WAIT', wait)
        self.assertNotIn('Invalidation:', wait)


class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        flow._inflight.clear()
        self.state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=11, user_id=11))
        self.render = AsyncMock()
        async def download(media, destination, **kw):
            destination.write(IMAGE)
        self.bot = types.SimpleNamespace(download=AsyncMock(side_effect=download))
        self.patches = [patch.object(api, 'configured', return_value=True),
            patch.object(api, 'reserve', AsyncMock(return_value=None)),
            patch.object(api, 'analyse', AsyncMock(return_value=VALID)),
            patch.object(localization, 'language_for', AsyncMock(return_value='en')),
            patch.object(Message, 'answer', AsyncMock())]
        for p in self.patches:
            p.start()
        await self.open()

    async def asyncTearDown(self):
        for p in reversed(self.patches):
            p.stop()

    async def open(self):
        cb = types.SimpleNamespace(message=types.SimpleNamespace(chat=types.SimpleNamespace(type='private')),
                                   from_user=types.SimpleNamespace(id=11), answer=AsyncMock())
        await flow.open_screen(cb, self.bot, self.state, self.render)

    async def test_button_upload_and_result(self):
        self.assertEqual(await self.state.get_state(), flow.Chart.waiting_image.state)
        await flow.receive(message(), self.bot, self.state, self.render)
        api.analyse.assert_awaited_once_with(IMAGE, 'en', '')
        api.reserve.assert_awaited_once()
        self.assertIn('BUY', self.render.await_args.args[3])
        self.assertFalse(flow._inflight)

    async def test_document_supported_invalid_inputs_not_sent(self):
        document = dict(file_id='doc', file_unique_id='doc1', mime_type='image/png', file_size=40)
        await flow.receive(message(photo=None, document=document), self.bot, self.state, self.render)
        api.analyse.assert_awaited_once()
        api.analyse.reset_mock()
        for m in (message(photo=None, text='123456789'),
                  message(media_group_id='album'),
                  message(photo=None, document=dict(document, mime_type='application/pdf')),
                  message(photo=None, document=dict(document, file_size=api.MAX_IMAGE_BYTES + 1))):
            await flow.receive(m, self.bot, self.state, self.render)
        api.analyse.assert_not_awaited()

    async def test_budget_and_missing_key_block_provider(self):
        for reason in ('limit', 'cooldown'):
            api.reserve.return_value = reason
            await flow.receive(message(), self.bot, self.state, self.render)
            self.assertEqual(Message.answer.await_args.args[0], flow.MESSAGES[reason])
        api.analyse.assert_not_awaited()
        with patch.object(api, 'configured', return_value=False):
            await self.open()
            self.assertIsNone(await self.state.get_state())
            self.assertEqual(self.render.await_args.args[3], flow.MESSAGES['configuration'])

    async def test_cancel_and_duplicate_upload(self):
        started, finish = asyncio.Event(), asyncio.Event()
        async def analyse(*args):
            started.set()
            await finish.wait()
            return VALID
        api.analyse.side_effect = analyse
        task = asyncio.create_task(flow.receive(message(), self.bot, self.state, self.render))
        await asyncio.wait_for(started.wait(), 3)
        await flow.receive(message(), self.bot, self.state, self.render)
        api.analyse.assert_awaited_once()
        self.assertEqual(Message.answer.await_args.args[0], flow.MESSAGES['busy'])
        await self.state.clear()
        self.render.reset_mock()
        finish.set()
        await task
        self.render.assert_not_awaited()
        self.assertFalse(flow._inflight)

    async def test_new_session_cannot_receive_old_result(self):
        started, finish = asyncio.Event(), asyncio.Event()
        async def analyse(*args):
            started.set()
            await finish.wait()
            return VALID
        api.analyse.side_effect = analyse
        task = asyncio.create_task(flow.receive(message(), self.bot, self.state, self.render))
        await asyncio.wait_for(started.wait(), 3)
        await self.open()
        self.render.reset_mock()
        finish.set()
        await task
        self.render.assert_not_awaited()

    async def test_provider_failure_is_visible_and_releases_user(self):
        api.analyse.side_effect = api.AnalysisError('service')
        await flow.receive(message(), self.bot, self.state, self.render)
        self.assertEqual(self.render.await_args.args[3], flow.MESSAGES['service'])
        self.assertFalse(flow._inflight)

    async def test_different_users_do_not_share_results(self):
        second_state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=12, user_id=12))
        await second_state.set_state(flow.Chart.waiting_image.state)
        await second_state.update_data(chart_session='other-user')
        async def analyse(image, language, context):
            await asyncio.sleep(0)
            return dict(VALID, direction='SELL' if context == 'second' else 'BUY')
        api.analyse.side_effect = analyse
        await asyncio.gather(flow.receive(message(), self.bot, self.state, self.render),
            flow.receive(message(uid=12, caption='second'), self.bot, second_state, self.render))
        results = {call.args[1]: call.args[3] for call in self.render.await_args_list
                   if 'Chart Analysis' in call.args[3] and call.args[3] != flow.PROMPT}
        self.assertIn('BUY', results[11])
        self.assertIn('SELL', results[12])

    async def test_global_concurrency_refuses_before_download(self):
        flow._inflight.update(range(100, 100 + api.MAX_CONCURRENT))
        await flow.receive(message(), self.bot, self.state, self.render)
        self.bot.download.assert_not_awaited()
        api.analyse.assert_not_awaited()

    async def test_private_chat_only(self):
        cb = types.SimpleNamespace(message=types.SimpleNamespace(chat=types.SimpleNamespace(type='group')),
                                   from_user=types.SimpleNamespace(id=11), answer=AsyncMock())
        self.render.reset_mock()
        await flow.open_screen(cb, self.bot, self.state, self.render)
        self.render.assert_not_awaited()
        self.assertEqual(cb.answer.await_args.args[0], flow.MESSAGES['private'])

    async def test_dispatch_routes_numeric_text_to_chart_not_verification(self):
        import test_signal_flow as H
        fake_db = H._install_stub_modules()
        fake_db.pool = None
        mod = H._load_bot()
        # Test the actual dispatcher registration against an active chart FSM.
        bot = Bot('123456:TEST')
        state = mod.dp.fsm.get_context(bot=bot, chat_id=11, user_id=11)
        await state.set_state(flow.Chart.waiting_image.state)
        with patch.object(flow, 'receive', AsyncMock()) as receive:
            from aiogram.types import Update
            await mod.dp.feed_update(bot, Update(update_id=1, message=message(photo=None, text='123456789')))
            receive.assert_awaited_once()
        await bot.session.close()


if __name__ == '__main__':
    unittest.main()
