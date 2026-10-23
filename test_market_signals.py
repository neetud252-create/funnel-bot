"""Asset flow: shared quotas, cancellation, graph delivery and fast double taps."""
import asyncio
import os
import time
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault('DATABASE_URL', 'postgresql://unused')
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BufferedInputFile
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendPhoto
import market_data as data
import market_signals as flow
from test_market_data import ASSET, payload
from test_chart_signals import VALID
import test_signal_flow as H


class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = H._install_stub_modules()
        self.db.pool = None
        self.state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=11, user_id=11))
        self.render = AsyncMock()
        self.bot = types.SimpleNamespace()
        self.cb = H.FakeCB(11, 'market:a:'+ASSET.key, 123)
        self.snapshot = data.parse_candles(payload(now=time.time()), ASSET, 60)
        self.feed = types.SimpleNamespace(assets=AsyncMock(return_value=(ASSET,)),
                                         candles=AsyncMock(return_value=self.snapshot))
        self.progress = types.SimpleNamespace(start=Mock(), stop=AsyncMock(), retrying=False)
        self.patches = [
            patch.object(data, 'enabled', return_value=True),
            patch.object(data, 'feed', return_value=self.feed),
            patch.object(flow.api, 'configured', return_value=True),
            patch.object(flow.trial, 'status', AsyncMock(return_value=(False, 2))),
            patch.object(flow.trial, 'reserve', AsyncMock(return_value=(None, 'slot'))),
            patch.object(flow.trial, 'consume', AsyncMock(return_value=True)),
            patch.object(flow.trial, 'release', AsyncMock()),
            patch.object(flow.api, 'reserve', AsyncMock(return_value=None)),
            patch.object(flow.api, 'analyse', AsyncMock(return_value=VALID)),
            patch.object(flow.localization, 'language_for', AsyncMock(return_value='en')),
            patch.object(flow.market_chart, 'render', return_value=b'PNG'),
            patch.object(flow.chart_progress, 'Progress', return_value=self.progress),
        ]
        for p in self.patches:
            p.start()
        flow.chart_signals._inflight.clear()

    async def asyncTearDown(self):
        for p in reversed(self.patches):
            p.stop()
        flow.chart_signals._inflight.clear()

    async def analyse(self):
        await flow.analyse(self.cb, self.bot, self.state, self.render, ASSET.key)

    async def test_result_sends_chart_and_analysis_consuming_one_shared_slot(self):
        await self.analyse()
        args = self.render.await_args.args
        self.assertIsInstance(args[2], BufferedInputFile)
        self.assertEqual(args[2].data, b'PNG')
        self.assertIn('EUR/USD OTC', args[3])
        self.assertIn('BUY bias', args[3])
        self.assertIn('60 seconds', flow.api.analyse.await_args.args[2])
        flow.trial.reserve.assert_awaited_once_with(None, 11, -123)
        flow.trial.consume.assert_awaited_once_with(None, 'slot')
        self.assertTrue(self.render.await_args.kwargs['raise_on_error'])
        self.assertFalse(flow.chart_signals._inflight)

    async def test_incomplete_setup_becomes_wait(self):
        flow.api.analyse.return_value = dict(VALID, checks=dict(VALID['checks'], follow_through=False))
        await self.analyse()
        self.assertIn('WAIT', self.render.await_args.args[3])
        self.assertNotIn('BUY bias', self.render.await_args.args[3])

    async def test_fast_double_tap_claims_before_callback_acknowledgement(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed_answer(*args, **kwargs):
            entered.set()
            await release.wait()
        self.cb.answer = delayed_answer
        first = asyncio.create_task(self.analyse())
        await entered.wait()
        second = H.FakeCB(11, self.cb.data, 124)
        await flow.analyse(second, self.bot, self.state, self.render, ASSET.key)
        self.assertTrue(second.answers[0][1])
        self.assertIn(11, flow.chart_signals._inflight)
        release.set()
        await first
        flow.trial.reserve.assert_awaited_once()
        flow.api.analyse.assert_awaited_once()

    async def test_shared_upload_inflight_and_usage_limits_block_asset_requests(self):
        flow.chart_signals._inflight.add(11)
        await self.analyse()
        flow.trial.reserve.assert_not_awaited()
        flow.chart_signals._inflight.clear()
        flow.trial.status.return_value = (False, 0)
        await self.analyse()
        flow.trial.reserve.assert_not_awaited()
        self.feed.candles.assert_not_awaited()

    async def test_feed_failure_or_provider_failure_does_not_spend_analysis(self):
        for failure in ('stale', 'gaps', 'closed', 'plan', 'quota'):
            self.feed.candles.side_effect = data.FeedError(failure)
            await self.analyse()
            flow.trial.consume.assert_not_awaited()
            flow.api.analyse.assert_not_awaited()
            self.assertFalse(flow.chart_signals._inflight)
        self.feed.candles.side_effect = None
        flow.api.analyse.side_effect = flow.api.AnalysisError('service')
        await self.analyse()
        flow.trial.consume.assert_not_awaited()

    async def test_navigation_cancels_old_result_without_spending_allowance(self):
        async def navigate(*args, **kwargs):
            await self.state.clear()
            return VALID
        flow.api.analyse.side_effect = navigate
        await self.analyse()
        flow.trial.consume.assert_not_awaited()
        self.assertFalse(any(isinstance(c.args[2], BufferedInputFile) for c in self.render.await_args_list))

    async def test_stale_after_ai_does_not_deliver_or_consume(self):
        with patch.object(data.Snapshot, 'require_fresh', side_effect=data.FeedError('stale')):
            await self.analyse()
        flow.trial.consume.assert_not_awaited()
        self.assertIn('Fresh candles', self.render.await_args.args[3])

    async def test_private_gate_and_disabled_feature_never_fetch_feed(self):
        self.cb.message.chat.type = 'group'
        await flow.open_screen(self.cb, self.bot, self.state, self.render)
        self.feed.assets.assert_not_awaited()
        self.assertTrue(self.cb.answers[0][1])
        with patch.object(data, 'enabled', return_value=False):
            await flow.open_picker(self.bot, 11, self.state, self.render)
        self.feed.assets.assert_not_awaited()
        self.assertIn('being connected', self.render.await_args.args[3])

    async def test_cancelled_task_releases_slot_and_global_inflight(self):
        flow.api.analyse.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.analyse()
        flow.trial.release.assert_awaited()
        self.assertFalse(flow.chart_signals._inflight)

    async def test_definitive_telegram_rejection_refunds_and_uncertain_delivery_does_not(self):
        for error, refund in ((TelegramBadRequest(method=SendPhoto(chat_id=11, photo='test'), message='rejected'), True),
                              (TimeoutError('unknown delivery'), False)):
            flow.trial.release.reset_mock()
            async def render(*args, **kwargs):
                if isinstance(args[2], BufferedInputFile):
                    raise error
            self.render.side_effect = render
            await self.analyse()
            refunded = any(c.kwargs.get('delivery_rejected') for c in flow.trial.release.await_args_list)
            self.assertEqual(refunded, refund)

    async def test_render_accepts_generated_png_with_caption_and_inline_buttons(self):
        module = H._load_bot()
        bot = H.FakeBot()
        await module.render(bot, 11, BufferedInputFile(b'PNG', filename='chart.png'),
                            'Chart test', [[('Again', 'cb:market:open')]], raise_on_error=True)
        photo = next(c for c in bot.calls if c['kind'] == 'photo')
        self.assertEqual(photo['body'], 'Chart test')
        self.assertEqual(photo['markup'].inline_keyboard[0][0].callback_data, 'market:open')

    def test_all_assets_are_reachable_searchable_and_callbacks_fit_telegram(self):
        assets = tuple(data.Asset(f'ASSET{i}_otc', f'Asset {i:03}', True, None) for i in range(135))
        seen = set()
        for page in range(14):
            text, rows = flow.picker(assets, 60, page)
            seen.update(b[1] for row in rows for b in row if b[1].startswith('cb:market:a:'))
            self.assertTrue(all(len(b[1][3:].encode()) <= 64 for row in rows for b in row))
        self.assertEqual(len(seen), 135)
        self.assertEqual(flow.filtered((ASSET,), 'EUR/USD'), [ASSET])
        text, rows = flow.picker(assets, 300, 0, '<unknown>')
        self.assertIn('&lt;unknown&gt;', text)
        self.assertIn('No matching assets', text)


if __name__ == '__main__':
    unittest.main()
