"""Offline import-to-signal contracts; no real video, provider or DB requests."""
import asyncio
import copy
import json
import os
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault('DATABASE_URL', 'postgresql://unused')
import httpx
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message
import chart_analysis as api
import chart_signals as signals
import strategy_flow as flow
import youtube_strategy as yt
from test_chart_signals import IMAGE, VALID, message, response

VIDEO = 'aB_12345-xy'
TRANSCRIPT = ('Show a five minute chart with EMA 20. Buy when a completed candle crosses above EMA 20. '
              'Sell when a completed candle crosses below EMA 20. Avoid entry when the EMA is flat.')
def rule(text):
    return {'condition': text, 'source_quote': text}
RULES = {'usable': True, 'name': 'EMA 20 cross', 'summary': 'Trade a completed cross on the five minute chart.',
         'limitations': 'The EMA period and completed candle must be visible.',
         'indicators': [{'kind': 'EMA', 'period': 20, 'source_quote': 'Show a five minute chart with EMA 20.'}],
         'requirements': [rule('Show a five minute chart with EMA 20.')],
         'buy_rules': [rule('Buy when a completed candle crosses above EMA 20.')],
         'sell_rules': [rule('Sell when a completed candle crosses below EMA 20.')],
         'avoid_rules': [rule('Avoid entry when the EMA is flat.')]}
SAVED = {'version': yt.VERSION, 'video_id': VIDEO, 'origin': 'captions', 'rules': RULES}
SIGNAL = {key: VALID[key] for key in ('direction', 'asset', 'timeframe', 'reason', 'trend',
                                    'momentum', 'invalidation', 'evidence')}
SIGNAL.update(chart_readable=True, requirements=[True], buy_rules=[True], sell_rules=[False], avoid_rules=[False])


def envelope(value, provider):
    if provider == 'gemini':
        return response(value)
    return {'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant',
            'status': 'completed', 'content': [{'type': 'output_text', 'text': json.dumps(value)}]}]}


class RulesTests(unittest.TestCase):
    def test_urls_canonicalize_without_fetching_arbitrary_hosts(self):
        for url in ('https://www.youtube.com/watch?v=' + VIDEO + '&t=50',
                    'youtu.be/' + VIDEO, 'https://m.youtube.com/shorts/' + VIDEO,
                    'https://youtube.com/live/' + VIDEO, 'http://youtube.com/embed/' + VIDEO):
            self.assertEqual(yt.video_id(url), VIDEO)
        for url in ('https://youtube.com.evil.example/watch?v=' + VIDEO,
                    'https://youtube.com@127.0.0.1/watch?v=' + VIDEO,
                    'https://evil@youtube.com/watch?v=' + VIDEO,
                    'https://youtube.com:99/watch?v=' + VIDEO, 'file:///watch?v=' + VIDEO,
                    'https://youtube.com/watch?v=' + VIDEO + '&v=other',
                    'https://youtube.com/playlist?list=abc', VIDEO, None,
                    'https://youtu.be/' + VIDEO + '/extra'):
            with self.subTest(url=url), self.assertRaisesRegex(api.AnalysisError, '^video_url$'):
                yt.video_id(url)

    def test_grounding_rejects_hallucinated_or_incomplete_rules(self):
        self.assertEqual(yt.validate_strategy(copy.deepcopy(RULES), TRANSCRIPT), RULES)
        cases = []
        p=copy.deepcopy(RULES);p['buy_rules'][0]['source_quote']='The video never says this.';cases.append(p)
        p=copy.deepcopy(RULES);p['usable']='true';cases.append(p)
        p=copy.deepcopy(RULES);p['usable']=False;cases.append(p)
        p=copy.deepcopy(RULES);p['buy_rules']=[];p['sell_rules']=[];cases.append(p)
        p=copy.deepcopy(RULES);p['buy_rules']*=2;cases.append(p)
        p=copy.deepcopy(RULES);p['name']='x'*71;cases.append(p)
        p=copy.deepcopy(RULES);p['extra']='execute me';cases.append(p)
        for value in cases:
            with self.assertRaisesRegex(api.AnalysisError, '^strategy_rules$'):
                yt.validate_strategy(value, TRANSCRIPT)
        with self.assertRaisesRegex(api.AnalysisError, '^transcript_size$'):
            yt.normalize_transcript('x' * (yt.MAX_TRANSCRIPT_CHARS + 1))

    def test_custom_entries_preserve_both_directions_and_truthful_confirmation(self):
        self.assertTrue(yt.validate_signal(copy.deepcopy(SIGNAL), SAVED)['entry_confirmed'])
        sell = dict(SIGNAL, direction='SELL', buy_rules=[False], sell_rules=[True])
        self.assertEqual(yt.validate_signal(sell, SAVED)['direction'], 'SELL')
        for direction in ('BUY', 'SELL'):
            for fields in ({'requirements': [False]}, {'avoid_rules': [True]},
                           {'buy_rules': [False]}, {'sell_rules': [True]}):
                with self.subTest(direction=direction, fields=fields):
                    result = yt.validate_signal(dict(SIGNAL, direction=direction, **fields), SAVED)
                    self.assertEqual(result['direction'], direction)
                    self.assertFalse(result['entry_confirmed'])
                    for key, value in fields.items():
                        self.assertEqual(result[key], value)
        for fields in ({'buy_rules': []}, {'buy_rules': [1]}, {'evidence': ['x', ' X ']},
                       {'direction': 'SELL'}, {'direction': 'WAIT'}, {'invalidation': None}):
            with self.subTest(fields=fields), self.assertRaises(api.AnalysisError):
                yt.validate_signal(dict(SIGNAL, **fields), SAVED)
        with self.assertRaisesRegex(api.AnalysisError, '^response$') as caught:
            yt.validate_signal(dict(SIGNAL, direction=None, buy_rules=[False]), SAVED)
        self.assertEqual(caught.exception.detail, 'direction')
        with self.assertRaisesRegex(api.AnalysisError, '^chart$'):
            yt.validate_signal(dict(SIGNAL, direction=None, chart_readable=False), SAVED)
        # A one-sided lesson never falsely confirms the side with no entry rules.
        one_sided = copy.deepcopy(SAVED);one_sided['rules']['sell_rules']=[]
        self.assertEqual(yt.validate_signal(dict(SIGNAL, sell_rules=[]), one_sided)['direction'], 'BUY')
        estimate = yt.validate_signal(dict(SIGNAL, direction='SELL', buy_rules=[False], sell_rules=[]), one_sided)
        self.assertFalse(estimate['entry_confirmed'])

    def test_persisted_rules_validate_and_never_silently_fall_back(self):
        self.assertEqual(yt.decode_saved(json.dumps(SAVED)), SAVED)
        self.assertIsNone(yt.decode_saved(None))
        for value in ('{broken', {}, dict(SAVED, version='old'), dict(SAVED, video_id='bad')):
            with self.assertRaisesRegex(api.AnalysisError, '^strategy_saved$'):
                yt.decode_saved(value)

    def test_previous_saved_strategies_remain_readable_and_v2_checks_indicators(self):
        legacy = copy.deepcopy(SAVED)
        legacy['version'] = 'youtube-rules-v1'
        del legacy['rules']['indicators']
        self.assertEqual(yt.decode_saved(legacy), legacy)
        legacy['version'] = yt.VERSION
        with self.assertRaisesRegex(api.AnalysisError, '^strategy_saved$'):
            yt.decode_saved(legacy)
        for field, value in (('period', 0), ('period', True), ('period', 50.5),
                             ('period', 501), ('kind', 'execute'),
                             ('source_quote', 'This is not in the lesson.')):
            rules = copy.deepcopy(RULES)
            rules['indicators'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(api.AnalysisError):
                yt.validate_strategy(rules, TRANSCRIPT)

    def test_caption_typography_is_normalized_without_accepting_changed_words(self):
        rules = copy.deepcopy(RULES)
        rules['avoid_rules'] = [rule("Don't trade a flat EMA.")]
        transcript = TRANSCRIPT + ' Don’t trade a flat EMA.'
        self.assertEqual(yt.validate_strategy(rules, transcript), rules)
        rules['avoid_rules'][0]['source_quote'] = "Don't trade a rising EMA."
        with self.assertRaises(api.AnalysisError) as caught:
            yt.validate_strategy(rules, transcript)
        self.assertEqual(caught.exception.detail, 'strategy_quote')

    def test_preview_and_result_escape_output_and_remove_bias_boilerplate(self):
        saved = copy.deepcopy(SAVED);saved['rules']['name']='<b>Untrusted</b>'
        text = flow.summary(saved, 'en')
        self.assertIn('&lt;b&gt;Untrusted&lt;/b&gt;', text)
        self.assertIn('watch?v=' + VIDEO, text)
        result = dict(VALID, reason='Tentative bias — confirmation is incomplete or the evidence is mixed.\nSupport held; the next close is missing.')
        text = signals.result_text(result, 'en')
        self.assertNotIn('Tentative bias', text)
        self.assertNotIn('(tentative)', text)
        self.assertIn('Support held; the next close is missing.', text)


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_direction_repair_shares_the_original_analysis_deadline(self):
        async def slow(*args, **kwargs):
            await asyncio.sleep(0.1)
        with patch.object(api, 'TOTAL_TIMEOUT_SECONDS', 0.01), \
             patch.object(api, 'structured_request', slow):
            with self.assertRaisesRegex(api.AnalysisError, '^service$') as caught:
                await api.analyse(IMAGE, 'en', strategy=SAVED)
        self.assertEqual(caught.exception.detail, 'timeout')

    async def test_null_direction_repairs_once_with_same_strategy_and_budget(self):
        for provider in ('gemini', 'openai'):
            attempts = []
            def handler(req):
                attempts.append(json.loads(req.content))
                value = dict(SIGNAL, buy_rules=[False], direction=None if len(attempts) == 1 else 'SELL')
                return httpx.Response(200, json=envelope(value, provider))
            reserve = AsyncMock()
            with patch.dict(os.environ, CHART_PROVIDER=provider, GEMINI_API_KEY='fake', OPENAI_API_KEY='fake'):
                result = await api.analyse(IMAGE, 'en', strategy=SAVED, before_retry=reserve,
                                           transport=httpx.MockTransport(handler))
            self.assertEqual(result['direction'], 'SELL')
            self.assertFalse(result['entry_confirmed'])
            self.assertEqual(len(attempts), 2)
            reserve.assert_awaited_once()
            self.assertIn('validation_feedback', json.dumps(attempts[1]))
            self.assertIn(RULES['name'], json.dumps(attempts[1]))

    async def test_null_repair_cannot_repeat_or_bypass_cancellation_and_budget(self):
        count = 0
        def handler(req):
            nonlocal count
            count += 1
            return httpx.Response(200, json=response(dict(SIGNAL, direction=None, buy_rules=[False])))
        with patch.dict(os.environ, CHART_PROVIDER='gemini', GEMINI_API_KEY='fake'):
            with self.assertRaisesRegex(api.AnalysisError, '^response$'):
                await api.analyse(IMAGE, 'en', strategy=SAVED, transport=httpx.MockTransport(handler))
            self.assertEqual(count, 2)
            for code in ('limit', 'cancelled'):
                count = 0
                with self.assertRaisesRegex(api.AnalysisError, '^' + code + '$'):
                    await api.analyse(IMAGE, 'en', strategy=SAVED, transport=httpx.MockTransport(handler),
                                      before_retry=AsyncMock(side_effect=api.AnalysisError(code)))
                self.assertEqual(count, 1)

    async def test_repair_recovers_core_rules_and_reserves_a_second_request(self):
        for detail in ('unusable', 'quote'):
            rejected = copy.deepcopy(RULES)
            if detail == 'unusable':
                rejected['usable'] = False
            else:
                rejected['buy_rules'][0]['source_quote'] = 'not actually stated by this speaker'
            request = AsyncMock(side_effect=[rejected, copy.deepcopy(RULES)])
            async def structured(body, validator, **kwargs):
                return validator(await request(copy.deepcopy(body)))
            reserve = AsyncMock()
            with patch.object(api, 'structured_request', structured):
                result = await yt.generate(TRANSCRIPT, VIDEO, 'en', before_retry=reserve)
            self.assertEqual(result['rules'], RULES)
            self.assertEqual(request.await_count, 2)
            reserve.assert_awaited_once()
            feedback = request.await_args.args[0]['contents'][0]['parts'][1]['text']
            self.assertIn('validation_feedback', feedback)
            if detail == 'quote':
                data = json.loads(feedback)
                self.assertEqual(data['rejected_candidate'], rejected)
                self.assertEqual(data['invalid_quote_fields'], ['buy_rules[0].source_quote'])
                self.assertNotIn('not actually stated by this speaker',
                    request.await_args.args[0]['systemInstruction']['parts'][0]['text'])

    async def test_repair_is_bounded_and_cannot_bypass_budget_or_grounding(self):
        rejected = dict(RULES, usable=False)
        request = AsyncMock(return_value=rejected)
        async def structured(body, validator, **kwargs):
            return validator(await request())
        with patch.object(api, 'structured_request', structured):
            with self.assertRaisesRegex(api.AnalysisError, '^strategy_rules$'):
                await yt.generate(TRANSCRIPT, VIDEO, 'en')
            self.assertEqual(request.await_count, 2)
            request.reset_mock()
            with self.assertRaisesRegex(api.AnalysisError, '^limit$'):
                await yt.generate(TRANSCRIPT, VIDEO, 'en',
                                  before_retry=AsyncMock(side_effect=api.AnalysisError('limit')))
            request.assert_awaited_once()

    async def test_extraction_and_chart_use_each_configured_provider(self):
        for provider in ('gemini', 'openai'):
            seen = []
            def handler(req):
                seen.append(json.loads(req.content))
                return httpx.Response(200, json=envelope(RULES if len(seen) == 1 else SIGNAL, provider))
            with patch.dict(os.environ, CHART_PROVIDER=provider, GEMINI_API_KEY='fake', OPENAI_API_KEY='fake'):
                saved = await yt.generate(TRANSCRIPT, VIDEO, 'hi', transport=httpx.MockTransport(handler))
                result = await api.analyse(IMAGE, 'hi', strategy=saved, transport=httpx.MockTransport(handler))
            self.assertEqual(result['direction'], 'BUY')
            self.assertEqual(result['strategy_name'], RULES['name'])
            self.assertEqual(saved['origin'], 'captions')
            if provider == 'openai':
                self.assertEqual(len(seen[0]['input'][0]['content']), 1)
                self.assertEqual(seen[1]['input'][0]['content'][1]['type'], 'input_image')
                self.assertEqual(seen[0]['text']['format']['schema'], yt.STRATEGY_SCHEMA)
                instructions = seen[1]['instructions']
            else:
                self.assertEqual(len(seen[0]['contents'][0]['parts']), 1)
                self.assertEqual(seen[0]['generationConfig']['responseJsonSchema'], yt.STRATEGY_SCHEMA)
                instructions = seen[1]['systemInstruction']['parts'][0]['text']
            self.assertNotIn(RULES['name'], instructions)
            self.assertNotIn('LEVEL REACTION', instructions)
            self.assertIn('Hindi', instructions)

    async def test_caption_languages_timeout_and_errors(self):
        from youtube_transcript_api import YouTubeTranscriptApi
        english = Mock(language_code='en', is_generated=False)
        hindi = Mock(language_code='hi', is_generated=True)
        hindi.fetch.return_value = [types.SimpleNamespace(text=TRANSCRIPT)]
        with patch.object(YouTubeTranscriptApi, 'list', return_value=[english, hindi]):
            self.assertEqual(await yt.fetch_transcript(VIDEO, 'hi'), TRANSCRIPT)
        english.fetch.assert_not_called()
        with patch.object(YouTubeTranscriptApi, 'list', side_effect=RuntimeError('PRIVATE_URL')):
            with self.assertRaisesRegex(api.AnalysisError, '^transcript$'):
                await yt.fetch_transcript(VIDEO, 'en')
        with patch.object(YouTubeTranscriptApi, 'list', return_value=[hindi]):
            hindi.fetch.return_value = [types.SimpleNamespace(text='x' * (yt.MAX_TRANSCRIPT_CHARS + 1))]
            with self.assertRaisesRegex(api.AnalysisError, '^transcript_size$'):
                await yt.fetch_transcript(VIDEO, 'hi')


class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import test_signal_flow as H
        self.db = H._install_stub_modules()
        self.db.pool = None
        self.saved = {}
        async def save(uid, value):
            self.saved[uid] = copy.deepcopy(value)
        async def active(uid):
            return copy.deepcopy(self.saved.get(uid))
        self.db.set_chart_strategy = AsyncMock(side_effect=save)
        self.state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=11, user_id=11))
        self.render = AsyncMock()
        self.bot = types.SimpleNamespace(download=AsyncMock())
        signals._inflight.clear()
        self.patches = [patch.dict('os.environ', {'GEMINI_API_KEY': ''}),
                        patch.object(yt, 'get_active', AsyncMock(side_effect=active)),
                        patch.object(yt, 'fetch_transcript', AsyncMock(return_value=TRANSCRIPT)),
                        patch.object(yt, 'generate', AsyncMock(return_value=copy.deepcopy(SAVED))),
                        patch.object(api, 'configured', return_value=True),
                        patch.object(api, 'reserve', AsyncMock(return_value=None)),
                        patch.object(signals.trial, 'status', AsyncMock(return_value=(False, 2))),
                        patch.object(flow.localization, 'language_for', AsyncMock(return_value='en')),
                        patch.object(Message, 'answer', AsyncMock())]
        for p in self.patches:
            p.start();self.addCleanup(p.stop)

    def cb(self, data):
        return types.SimpleNamespace(data=data, from_user=types.SimpleNamespace(id=11), answer=AsyncMock(),
                                     message=message())

    async def open(self):
        await flow.open_screen(self.cb('strategy:open'), self.bot, self.state, self.render)

    async def import_video(self):
        await flow.receive(message(photo=None, text='https://youtu.be/' + VIDEO), self.bot, self.state, self.render)

    async def test_full_import_review_activation_and_default(self):
        await self.open();await self.import_video()
        self.assertEqual(await self.state.get_state(), flow.Strategy.preview.state)
        self.db.set_chart_strategy.assert_not_awaited()
        data = await self.state.get_data()
        await flow.action(self.cb('strategy:use:' + data['strategy_token']), self.bot, self.state, self.render)
        self.assertEqual(self.saved[11], SAVED)
        self.assertEqual(await self.state.get_state(), signals.Chart.waiting_image.state)
        self.assertEqual((await self.state.get_data())['chart_strategy'], SAVED)
        self.assertIsNone(await yt.get_active(12))
        await flow.action(self.cb('strategy:default'), self.bot, self.state, self.render)
        self.assertIsNone(self.saved[11])
        self.assertIsNone((await self.state.get_data())['chart_strategy'])

    async def test_activation_opens_live_markets_when_available(self):
        import market_data
        import market_signals
        await self.open();await self.import_video()
        token = (await self.state.get_data())['strategy_token']
        with patch.object(market_data, 'enabled', return_value=True):
            await flow.action(self.cb('strategy:use:' + token), self.bot, self.state, self.render)
        self.assertEqual(self.saved[11], SAVED)
        self.assertEqual(await self.state.get_state(), market_signals.Market.browsing.state)
        self.assertEqual(self.render.await_args.kwargs['screen_key'], 'market_type')

    async def test_unavailable_captions_accept_multipart_transcript(self):
        yt.fetch_transcript.side_effect = api.AnalysisError('transcript')
        await self.open();await self.import_video()
        self.assertEqual(await self.state.get_state(), flow.Strategy.transcript.state)
        for text in (TRANSCRIPT[:90], TRANSCRIPT[90:]):
            await flow.receive(message(photo=None, text=text), self.bot, self.state, self.render)
        await flow.action(self.cb('strategy:build'), self.bot, self.state, self.render)
        self.assertEqual(await self.state.get_state(), flow.Strategy.preview.state)
        self.assertEqual(yt.generate.await_args.kwargs['origin'], 'pasted')
        self.assertIn(TRANSCRIPT[:90], yt.generate.await_args.args[0])
        self.db.set_chart_strategy.assert_not_awaited()

    async def test_video_fallback_reserves_request_and_keeps_source_label(self):
        yt.fetch_transcript.side_effect = api.AnalysisError('transcript')
        with patch.object(flow.youtube_source, 'video_reading_configured', return_value=True), \
             patch.object(flow.youtube_source, 'read_video', AsyncMock(return_value=TRANSCRIPT)):
            await self.open();await self.import_video()
        self.assertEqual(await self.state.get_state(), flow.Strategy.preview.state)
        self.assertEqual(yt.generate.await_args.kwargs['origin'], 'video_analysis')
        self.assertEqual(api.reserve.await_count, 2)
        self.assertTrue(api.reserve.await_args.kwargs['retry'])
        preview = dict(SAVED, origin='video_analysis')
        self.assertIn('Video analysis', flow.summary(preview, 'en'))
        self.assertNotIn('Video captions', flow.summary(preview, 'en'))

    async def test_cancellation_duplicate_import_and_expired_preview(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def fetch(*args):
            started.set();await release.wait();return TRANSCRIPT
        yt.fetch_transcript.side_effect=fetch
        await self.open()
        task=asyncio.create_task(self.import_video());await started.wait()
        await self.import_video()
        yt.fetch_transcript.assert_awaited_once()
        await self.state.clear();release.set();await task
        yt.generate.assert_not_awaited()
        self.db.set_chart_strategy.assert_not_awaited()
        cb=self.cb('strategy:use:stale');await flow.action(cb,self.bot,self.state,self.render)
        self.assertEqual(cb.answer.await_args.args[0],flow.MESSAGES['expired'])
        self.assertNotIn(11,signals._inflight)

    async def test_import_budget_rejection_does_not_fetch_or_activate(self):
        api.reserve.return_value='limit'
        await self.open();await self.import_video()
        yt.fetch_transcript.assert_not_awaited()
        self.db.set_chart_strategy.assert_not_awaited()
        self.assertEqual(self.render.await_args.args[3], signals.MESSAGES['limit'])

    async def test_navigation_during_budget_check_suppresses_late_work(self):
        async def reserve(*args, **kwargs):
            await self.state.clear()
        api.reserve.side_effect = reserve
        await self.open()
        self.render.reset_mock()
        await self.import_video()
        yt.fetch_transcript.assert_not_awaited()
        self.render.assert_not_awaited()
        self.assertNotIn(11, signals._inflight)

    async def test_text_file_fallback_is_bounded_and_keeps_the_video(self):
        yt.fetch_transcript.side_effect = api.AnalysisError('transcript')
        await self.open();await self.import_video()
        async def download(media, destination, **kw):
            destination.write(TRANSCRIPT.encode('utf-8'))
        self.bot.download.side_effect = download
        document = dict(file_id='file', file_unique_id='file-one', file_name='lesson.txt', file_size=500)
        await flow.receive(message(photo=None, document=document), self.bot, self.state, self.render)
        data = await self.state.get_data()
        self.assertEqual(data['strategy_transcript'], TRANSCRIPT)
        self.assertEqual(data['strategy_video'], VIDEO)
        self.bot.download.reset_mock()
        document['file_size'] = yt.MAX_TRANSCRIPT_BYTES + 1
        await flow.receive(message(photo=None, document=document), self.bot, self.state, self.render)
        self.bot.download.assert_not_awaited()

    async def test_save_failure_preserves_preview_and_previous_strategy(self):
        self.saved[11] = SAVED
        await self.open();await self.import_video()
        data = await self.state.get_data()
        self.db.set_chart_strategy.side_effect = RuntimeError('PRIVATE')
        cb = self.cb('strategy:use:' + data['strategy_token'])
        await flow.action(cb, self.bot, self.state, self.render)
        self.assertEqual(self.saved[11], SAVED)
        self.assertEqual(await self.state.get_state(), flow.Strategy.preview.state)
        self.assertEqual(cb.answer.await_args.args[0], signals.MESSAGES['service'])
        self.assertNotIn(11, signals._inflight)

    async def test_private_chat_only(self):
        m=message(photo=None,text='https://youtu.be/'+VIDEO,chat={'id':11,'type':'group'})
        await flow.receive(m,self.bot,self.state,self.render)
        yt.fetch_transcript.assert_not_awaited()

    async def test_dispatch_routes_command_and_links_before_chart_or_uid_handlers(self):
        import test_signal_flow as H
        from aiogram.types import Update
        mod = H._load_bot()
        bot = Bot('123456:TEST')
        state = mod.dp.fsm.get_context(bot=bot, chat_id=11, user_id=11)
        try:
            with patch.object(flow, 'receive', AsyncMock()) as receive, \
                 patch.object(signals, 'receive', AsyncMock()) as chart, \
                 patch.object(flow, 'open_screen', AsyncMock()) as opened:
                await state.set_state(signals.Chart.waiting_image)
                await mod.dp.feed_update(bot, Update(update_id=901,
                    message=message(photo=None, text='https://youtu.be/' + VIDEO)))
                receive.assert_awaited_once();chart.assert_not_awaited()
                await mod.dp.feed_update(bot, Update(update_id=903, message=message()))
                chart.assert_awaited_once()
                receive.reset_mock()
                await state.set_state('Market:browsing')
                await mod.dp.feed_update(bot, Update(update_id=904,
                    message=message(photo=None, text='https://youtu.be/' + VIDEO)))
                receive.assert_awaited_once()
                await state.set_state(flow.Strategy.transcript)
                await mod.dp.feed_update(bot, Update(update_id=902,
                    message=message(photo=None, text='/strategy', entities=[{'type':'bot_command','offset':0,'length':9}])))
                opened.assert_awaited_once()
        finally:
            await bot.session.close()

    async def test_custom_strategy_reaches_chart_provider_and_service_failure_refunds(self):
        self.saved[11]=SAVED
        await signals.open_screen(self.cb('chart:upload'),self.bot,self.state,self.render,upload=True)
        async def download(media,destination,**kw):
            destination.write(IMAGE)
        self.bot.download.side_effect=download
        with patch.object(signals.trial,'reserve',AsyncMock(return_value=(None,'slot'))), \
             patch.object(signals.trial,'consume',AsyncMock()) as consume, \
             patch.object(signals.trial,'release',AsyncMock()) as release, \
             patch.object(api,'analyse',AsyncMock(side_effect=api.AnalysisError('service'))) as analyse:
            await signals.receive(message(),self.bot,self.state,self.render)
            self.assertEqual(analyse.await_args.kwargs['strategy'],SAVED)
            consume.assert_not_awaited()
            release.assert_awaited_once_with(None,'slot')
            self.assertEqual(self.render.await_args.args[3],signals.MESSAGES['service'])

    async def test_uploaded_custom_chart_delivers_incomplete_buy_and_sell(self):
        self.saved[11] = SAVED
        async def download(media, destination, **kw):
            destination.write(IMAGE)
        self.bot.download.side_effect = download
        for direction in ('BUY', 'SELL'):
            await signals.open_screen(self.cb('chart:upload'), self.bot, self.state, self.render, upload=True)
            result = yt.validate_signal(dict(SIGNAL, direction=direction, buy_rules=[False],
                reason='Pullback confirmation is missing; recent completed price movement favors ' + direction), SAVED)
            with patch.object(signals.trial, 'reserve', AsyncMock(return_value=(None, 'slot'))), \
                 patch.object(signals.trial, 'consume', AsyncMock(return_value=True)) as consume, \
                 patch.object(signals.trial, 'release', AsyncMock()), \
                 patch.object(api, 'analyse', AsyncMock(return_value=result)):
                await signals.receive(message(), self.bot, self.state, self.render)
                self.assertEqual(self.render.await_args.kwargs['screen_key'], 'chart_result')
                self.assertIn(direction, self.render.await_args.args[3])
                self.assertIn('Pullback confirmation is missing', self.render.await_args.args[3])
                consume.assert_awaited_once_with(None, 'slot')


if __name__ == '__main__':
    unittest.main()
