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
import market_catalog as catalog
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
        self.progress = types.SimpleNamespace(start=AsyncMock(), stop=AsyncMock(), reconnecting=AsyncMock())
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
            patch.object(flow.market_progress, 'Loading', return_value=self.progress),
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
        self.assertIn('🟢 BUY', args[3])
        self.assertIn('Expiration:</b> M1', args[3])
        self.assertIn('Confirmed setup', args[3])
        self.assertIn('60 seconds', flow.api.analyse.await_args.args[2])
        flow.trial.reserve.assert_awaited_once_with(None, 11, -123)
        flow.trial.consume.assert_awaited_once_with(None, 'slot')
        self.assertTrue(self.render.await_args.kwargs['raise_on_error'])
        self.assertFalse(flow.chart_signals._inflight)

    async def test_incomplete_setup_retains_strategy_direction_with_tentative_label(self):
        for direction, setup in (('BUY', 'directional_buy'), ('SELL', 'directional_sell')):
            flow.api.analyse.return_value = dict(VALID, direction=direction, setup=setup,
                                                checks=dict(VALID['checks'], follow_through=False))
            await self.analyse()
            self.assertNotIn('WAIT', self.render.await_args.args[3])
            self.assertIn(direction, self.render.await_args.args[3])
            self.assertIn('Tentative bias', self.render.await_args.args[3])

    async def test_invalid_strategy_result_never_fabricates_or_consumes(self):
        for result in (dict(VALID, direction=None), dict(VALID, direction='WAIT'),
                       dict(VALID, direction='SELL'), dict(VALID, evidence=[])):
            flow.api.analyse.return_value = result
            await self.analyse()
            self.assertEqual(self.render.await_args.kwargs['screen_key'], 'market_error')
        flow.trial.consume.assert_not_awaited()

    async def test_all_expiries_preserved_without_inventing_candle_intervals(self):
        callbacks = []
        dp = types.SimpleNamespace(callback_query=types.SimpleNamespace(register=lambda h, *f: callbacks.append(h)),
                                   message=types.SimpleNamespace(register=lambda *a: None))
        flow.install(dp, self.render)
        key = catalog.find(catalog.entries((ASSET,)), ASSET.key).key
        for expiry in flow.EXPIRIES:
            await self.state.update_data(market_period=300, market_expiry=10)
            cb = H.FakeCB(11, f'market:expire:{key}:{expiry}', 123)
            await callbacks[0](cb, self.bot, self.state)
            self.feed.candles.assert_awaited_with(ASSET.key, 60)
            self.assertIn(f'Expiration:</b> M{expiry} · {expiry} min', self.render.await_args.args[3])
            self.assertIn('Chart candles:</b> M1', self.render.await_args.args[3])
            self.assertIn(f'User-selected expiration: {expiry} minutes', flow.api.analyse.await_args.args[2])
            self.assertIn(f'cb:market:expire:{key}:{expiry}', str(self.render.await_args.args[4]))
        self.assertEqual(self.progress.start.await_count, 10)

    async def test_locked_and_malformed_expiries_never_start_analysis(self):
        callbacks = []
        dp = types.SimpleNamespace(callback_query=types.SimpleNamespace(register=lambda h, *f: callbacks.append(h)),
                                   message=types.SimpleNamespace(register=lambda *a: None))
        flow.install(dp, self.render)
        for action in [f'market:locked:{n}' for n in flow.LOCKED_SECONDS] + [
                f'market:expire:{ASSET.key}:{n}' for n in ('0', '11', '-1', '5.0', 'abc')]:
            cb = H.FakeCB(11, action, 123)
            await callbacks[0](cb, self.bot, self.state)
            self.assertTrue(cb.answers[0][1])
        flow.trial.reserve.assert_not_awaited()
        flow.api.analyse.assert_not_awaited()
        self.progress.start.assert_not_awaited()

    def test_reference_expiration_grid_shape_style_and_callbacks(self):
        rows = flow.expiration_rows(ASSET.key)
        self.assertEqual([len(r) for r in rows], [1, 2, 3, 3, 1, 2, 3, 3, 1])
        self.assertEqual([b[0] for r in rows for b in r], [
            '🔒 S5', '🔒 S10', '🔒 S15', '🔒 S20', '🔒 S25', '🔒 S30',
            '🔒 S45', '🔒 S50', '🔒 S55', '🔥 M1', 'M2', 'M3',
            'M4', 'M5', 'M6', 'M7', 'M8', 'M9', '✅ M10'])
        self.assertTrue(all(b[2] == 'primary' and len(b[1][3:].encode()) <= 64 for r in rows for b in r))

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

    async def test_exhausted_allowance_can_browse_but_cannot_run_analysis(self):
        for verified in (False, True):
            flow.trial.status.return_value = (verified, 0)
            await flow.open_picker(self.bot, 11, self.state, self.render)
            self.assertIn('Select market type', self.render.await_args.args[3])
            self.assertEqual(await self.state.get_state(), flow.Market.browsing.state)
            await self.analyse()
            self.assertEqual(self.render.await_args.kwargs['screen_key'], 'market_limit')
        flow.trial.reserve.assert_not_awaited()
        self.feed.candles.assert_not_awaited()
        flow.api.analyse.assert_not_awaited()

    async def test_exhausted_allowance_can_change_timeframe_page_and_search(self):
        flow.trial.status.return_value = (False, 0)
        callbacks, messages = [], []
        dp = types.SimpleNamespace(
            callback_query=types.SimpleNamespace(register=lambda handler, *filters: callbacks.append(handler)),
            message=types.SimpleNamespace(register=lambda handler, *filters: messages.append(handler)))
        flow.install(dp, self.render)
        await flow.open_picker(self.bot, 11, self.state, self.render)
        for action in ('market:cat:otc:forex', 'market:list:otc:forex:9', 'market:all'):
            cb = H.FakeCB(11, action, 123)
            await callbacks[0](cb, self.bot, self.state)
            self.assertEqual(self.render.await_args.kwargs['screen_key'], 'market_assets')
        message = types.SimpleNamespace(chat=types.SimpleNamespace(type='private'),
                                        from_user=types.SimpleNamespace(id=11), text='EUR/USD')
        await messages[-1](message, self.bot, self.state)
        self.assertIn('EUR/USD OTC', str(self.render.await_args.args[4]))
        flow.trial.status.assert_not_awaited()
        flow.trial.reserve.assert_not_awaited()
        self.feed.candles.assert_not_awaited()

    async def test_feed_failure_or_provider_failure_does_not_spend_analysis(self):
        for failure in ('stale', 'gaps', 'closed', 'plan', 'quota'):
            self.feed.candles.side_effect = data.FeedError(failure)
            await self.analyse()
            flow.trial.consume.assert_not_awaited()
            flow.api.analyse.assert_not_awaited()
            self.assertFalse(flow.chart_signals._inflight)
            self.progress.stop.assert_awaited()
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
        self.assertIn('Select market type', self.render.await_args.args[3])
        await flow.show_categories(self.bot, 11, self.state, self.render, 'fin')
        self.assertIn('Select an asset by category', self.render.await_args.args[3])

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
        assets = tuple(catalog.Entry(f'Asset {i:03} OTC', 'stocks', 'otc') for i in range(135))
        seen = set()
        for page in range(23):
            text, rows = flow.picker(assets, 'otc', 'stocks', page)
            seen.update(b[1] for row in rows for b in row if b[1].startswith('cb:market:a:'))
            self.assertTrue(all(len(b[1][3:].encode()) <= 64 for row in rows for b in row))
        self.assertEqual(len(seen), 135)
        self.assertEqual(flow.filtered((ASSET,), 'EUR/USD'), [ASSET])
        text, rows = flow.picker(assets, 'otc', 'stocks', 0, '<unknown>')
        self.assertIn('&lt;unknown&gt;', text)
        self.assertIn('No matching assets', text)

    async def test_unavailable_assets_do_not_fetch_candles_reserve_or_fabricate_analysis(self):
        unavailable = next(e for e in catalog.SEED if e.name == 'AUD/CAD OTC')
        fin = next(e for e in catalog.SEED if e.market == 'fin')
        for entry in (unavailable, fin):
            await flow.select_asset(self.bot, 11, self.state, self.render, entry.key)
            self.assertEqual(self.render.await_args.args[2], 'expiration_time')
            self.assertIn('cb:market:expire:', str(self.render.await_args.args[4]))
            # A forged/stale run callback is checked server-side too.
            await flow.analyse(self.cb, self.bot, self.state, self.render, entry.key)
        self.feed.candles.assert_not_awaited()
        flow.trial.reserve.assert_not_awaited()
        flow.api.analyse.assert_not_awaited()

    async def test_plan_upgrade_unlocks_existing_catalogue_item_without_deploy(self):
        entry = next(e for e in catalog.SEED if e.name == 'AUD/CAD OTC')
        await flow.select_asset(self.bot, 11, self.state, self.render, entry.key)
        self.assertIn('not available', self.render.await_args.args[3])
        self.feed.assets.return_value = (ASSET, data.Asset('AUDCAD_otc', entry.name, True, None))
        await flow.select_asset(self.bot, 11, self.state, self.render, entry.key)
        self.assertIn('Choose the expiration time', self.render.await_args.args[3])
        self.assertNotIn('not available', self.render.await_args.args[3])
        self.assertIn(f'cb:market:expire:{entry.key}:5', str(self.render.await_args.args[4]))
        flow.trial.reserve.assert_not_awaited()

    async def test_fin_uses_matching_live_feed_and_preserves_source(self):
        from dataclasses import replace
        asset = data.Asset('EURUSD', 'EUR/USD', True, 80, 'forex')
        self.feed.assets.return_value = (asset, ASSET)
        self.feed.candles.return_value = replace(self.snapshot, asset=asset, source='Pocket Option demo feed')
        entry = next(e for e in catalog.entries((asset, ASSET)) if e.name == 'EUR/USD')
        await flow.select_asset(self.bot, 11, self.state, self.render, entry.key)
        self.assertNotIn('not connected', self.render.await_args.args[3])
        await flow.analyse(self.cb, self.bot, self.state, self.render, entry.key)
        self.feed.candles.assert_awaited_once_with(asset.key, 60)
        self.assertIn('Pocket Option demo feed', self.render.await_args.args[3])
        self.assertNotIn('OTCharts', self.render.await_args.args[3])
        flow.trial.consume.assert_awaited_once()

    async def test_market_navigation_cancels_pending_catalogue_response(self):
        async def navigating():
            await flow.open_picker(self.bot, 11, self.state, self.render)
            return (ASSET,)
        await flow.navigate(self.state, market_book='otc', market_category='forex')
        self.feed.assets.side_effect = navigating
        await flow.show_picker(self.bot, 11, self.state, self.render)
        self.assertEqual(self.render.await_args.kwargs['screen_key'], 'market_type')

    def test_reference_currency_order_page_shape_and_complete_public_catalogue(self):
        import config
        entries = [e for e in catalog.SEED if e.market == 'otc' and e.category == 'forex']
        self.assertEqual([e.name for e in entries], config.PAIRS_RAW)
        self.assertEqual(len(catalog.SEED), 172)
        self.assertEqual(len({e.key for e in catalog.SEED}), 172)
        for page in range(10):
            text, rows = flow.picker(entries, page=page)
            assets = [r for r in rows if r[0][1].startswith('cb:market:a:')]
            self.assertEqual([len(r) for r in assets], [2, 2, 2] if page < 9 else [2, 1])
            self.assertIn(f'{page+1}/10', str(rows))
            self.assertEqual(rows[-1], [('« Back', 'cb:market:book:otc', 'danger')])
        for entry in catalog.SEED:
            self.assertIn(entry.category, catalog.CATEGORIES)

    async def test_public_catalogue_available_without_key_or_generation_permission(self):
        with patch.object(data, 'enabled', return_value=False):
            await flow.open_picker(self.bot, 11, self.state, self.render)
            await flow.show_categories(self.bot, 11, self.state, self.render, 'otc')
            await flow.navigate(self.state, market_book='otc', market_category='forex')
            await flow.show_picker(self.bot, 11, self.state, self.render, 9)
            self.assertIn('KES/USD OTC', str(self.render.await_args.args[4]))
            await self.analyse()
        self.feed.assets.assert_not_awaited()
        self.feed.candles.assert_not_awaited()
        flow.trial.reserve.assert_not_awaited()

    async def test_installed_legacy_callbacks_and_uploads_only_open_market_menu(self):
        module = H._load_bot()
        bot = H.FakeBot()
        actions = ('chart:upload', 'mode:manual', 'type:otc', 'asset:forex',
                   'pair:eurusdotc', 'pairpage:2', 'm:1', 's:5', 'new_signal', 'go:test_menu')
        for action in actions:
            cb = H.FakeCB(11, action, 123)
            for handler in module.dp.callback_query.handlers:
                matched, _ = await handler.check(cb)
                if matched:
                    self.assertEqual(handler.callback.__name__, 'legacy', action)
                    await handler.callback(cb, bot, self.state)
                    break
            else:
                self.fail('Missing callback: ' + action)
            self.assertIn('Select market type', H.last_screen(bot)['body'])
        self.assertFalse(any(h.callback.__module__ == 'chart_signals' for h in module.dp.message.handlers))
        uploads = next(h.callback for h in module.dp.message.handlers if h.callback.__name__ == 'retired_upload')
        await uploads(types.SimpleNamespace(chat=types.SimpleNamespace(type='private'),
                                           from_user=types.SimpleNamespace(id=11)), bot, self.state)
        self.assertIn('Select market type', H.last_screen(bot)['body'])
        flow.api.analyse.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
