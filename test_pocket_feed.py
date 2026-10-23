"""Offline broker wire, completed-bar, reconnect and read-only transport checks."""
import asyncio
import json
import os
import types
import unittest
from unittest.mock import patch

import aiohttp
import market_data as data
import pocket_feed as direct

NOW = 1792790532
ASSET = data.Asset('EURUSD_otc', 'EUR/USD OTC', True, 92, 'forex')


def asset_row(symbol=ASSET.symbol, name=ASSET.name, opened=True, category='currency'):
    return [66, symbol, name, category, 1, 92, 60, 30, 3, 1, 0, 0, [], 0, opened, [{'time': 60}]]


def history(period=60, now=NOW, symbol=ASSET.symbol):
    end = int(now + direct.BROKER_OFFSET) // period * period
    # Wire order is time, OPEN, CLOSE, HIGH, LOW (not O,H,L,C).
    bars = [[t, 1.1, 1.15, 1.2, 1.0] for t in range(end-100*period, end-3*period, period)]
    ticks = []
    for t in range(end-6*period, int(now+direct.BROKER_OFFSET)+1):
        position = t % period
        price = 1.1 if position == 0 else 1.2 if position == 1 else 1.0 if position == 2 else 1.15
        ticks.append([float(t), price])
    return {'asset': symbol, 'period': period, 'candles': list(reversed(bars)), 'history': ticks}


class Parsing(unittest.TestCase):
    def test_wire_ohlc_order_clock_conversion_and_tick_tail(self):
        for period in direct.PERIODS:
            snapshot = direct.parse_history(history(period), ASSET, period, NOW)
            self.assertEqual(len(snapshot.candles), 100)
            self.assertEqual(snapshot.source, direct.SOURCE)
            self.assertEqual(snapshot.closed_at, NOW // period * period)
            self.assertEqual(snapshot.candles[-1], data.Candle(snapshot.closed_at-period, 1.1, 1.2, 1.0, 1.15))
            self.assertTrue(all(b.time+period <= NOW for b in snapshot.candles))

    def test_first_tick_interval_and_newest_server_bar_can_be_truncated(self):
        value = history(300)
        value['candles'][0][2] = 1.16
        value['history'] = value['history'][2:]
        snapshot = direct.parse_history(value, ASSET, 300, NOW)
        self.assertEqual(snapshot.candles[-1].close, 1.15)
        self.assertEqual(len(snapshot.candles), 100)

    def test_m5_uses_complete_m1_tail_when_tick_history_starts_mid_interval(self):
        value = history(300)
        end = (NOW+direct.BROKER_OFFSET)//300*300
        value['history'] = [r for r in value['history'] if r[0] >= end+1]
        minute_snapshot = direct.parse_history(history(), ASSET, 60, NOW)
        snapshot = direct.parse_history(value, ASSET, 300, NOW, minutes=minute_snapshot)
        self.assertEqual(snapshot.closed_at, NOW//300*300)
        self.assertEqual(len(snapshot.candles), 100)

    def test_stale_ticks_and_shifted_clock_cannot_make_old_prices_fresh(self):
        for delta, code in ((-7200, 'stale'), (7200, 'invalid'), (-120, 'stale')):
            with self.assertRaisesRegex(data.FeedError, '^'+code+'$'):
                direct.parse_history(history(now=NOW+delta), ASSET, 60, NOW)

    def test_partial_intervals_and_tick_gaps_never_invent_candles(self):
        value = history()
        end = (NOW+direct.BROKER_OFFSET)//60*60
        value['history'] = [r for r in value['history'] if not end-120+10 <= r[0] < end-120+20]
        with self.assertRaisesRegex(data.FeedError, '^gaps$'):
            direct.parse_history(value, ASSET, 60, NOW)
        value = history()
        value['history'] = [r for r in value['history'] if not end-120 <= r[0] < end-120+4]
        with self.assertRaisesRegex(data.FeedError, '^gaps$'):
            direct.parse_history(value, ASSET, 60, NOW)

    def test_mismatched_identity_bad_numbers_and_conflicting_bars_rejected(self):
        values = []
        for key, value in [('asset', 'GBPUSD_otc'), ('period', 300), ('history', [])]:
            values.append(dict(history(), **{key: value}))
        for field, value in [('candles', [1, 1, 2, 0, 3]), ('history', [NOW, float('nan')])]:
            item = history()
            item[field].append(value)
            values.append(item)
        item = history()
        item['candles'][1][2] = 1.16  # conflicts with fully covered broker ticks
        values.append(item)
        item = history()
        item['history'].append([item['history'][0][0], 9])
        values.append(item)
        for value in values:
            with self.assertRaisesRegex(data.FeedError, '^invalid$'):
                direct.parse_history(value, ASSET, 60, NOW)

    def test_catalogue_preserves_market_category_status_and_symbol(self):
        assets = direct.parse_assets([asset_row(), asset_row('EURUSD', 'EUR/USD', False),
                                     asset_row('#AAPL_otc', 'Apple OTC', category='stock')])
        by_symbol = {a.symbol:a for a in assets}
        self.assertEqual(by_symbol['#AAPL_otc'].category, 'stocks')
        self.assertEqual(by_symbol['EURUSD'].market, 'fin')
        self.assertFalse(by_symbol['EURUSD'].open)
        self.assertEqual(by_symbol[ASSET.symbol].key, ASSET.key)
        import market_catalog
        items = market_catalog.entries(assets)
        fin = next(e for e in items if e.name == 'EUR/USD')
        otc = next(e for e in items if e.name == 'EUR/USD OTC')
        self.assertEqual(fin.live.symbol, 'EURUSD')
        self.assertEqual(otc.live.symbol, 'EURUSD_otc')
        malformed = asset_row()
        malformed[14] = 'false'
        with self.assertRaises(data.FeedError):
            direct.parse_assets([malformed])

    def test_free_provider_ignores_otcharts_key_but_requires_public_flag(self):
        with patch.dict(os.environ, MARKET_DATA_PROVIDER='pocketoption', OTCHARTS_API_KEY='',
                        MARKET_DATA_PUBLIC_ENABLED='true'):
            self.assertTrue(data.configured())
            self.assertTrue(data.public_enabled())
            os.environ['MARKET_DATA_PUBLIC_ENABLED'] = 'false'
            self.assertFalse(data.public_enabled())
        with patch.dict(os.environ, MARKET_DATA_PROVIDER='invalid', OTCHARTS_API_KEY='unused'):
            self.assertFalse(data.configured())


class FakeSocket:
    def __init__(self, session):
        self.session = session
        self.queue = asyncio.Queue()
        self.closed = False
        self.frames = []
        self.queue.put_nowait(types.SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data='0{"sid":"guest"}'))

    def event(self, name, payload):
        for kind, value in [(aiohttp.WSMsgType.TEXT, '451-'+json.dumps([name, {'_placeholder': True, 'num': 0}])),
                            (aiohttp.WSMsgType.BINARY, json.dumps(payload).encode())]:
            self.queue.put_nowait(types.SimpleNamespace(type=kind, data=value))

    async def send_str(self, raw):
        self.frames.append(raw)
        if raw == '40':
            self.queue.put_nowait(types.SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data='40{"sid":"guest"}'))
        elif raw.startswith('42'):
            name, payload = json.loads(raw[2:])
            if name == 'auth':
                self.event('updateAssets', [asset_row(opened=self.session.opened)])
                self.event('NotAuthorized' if self.session.reject else 'successauth', {})
            elif name == 'changeSymbol' and not self.session.hold:
                # Unrelated late/wrong-symbol responses must never satisfy it.
                self.event('updateHistoryNewFast', history(symbol='GBPUSD_otc'))
                self.event('updateHistoryNewFast', self.session.payload if payload['period'] == 60 else history(payload['period']))

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.closed:
            raise StopAsyncIteration
        return await self.queue.get()

    async def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = False
        self.opened = True
        self.hold = self.reject = False
        self.payload = history()

    async def ws_connect(self, url, **kwargs):
        self.url, self.ws_kwargs = url, kwargs
        self.ws = FakeSocket(self)
        return self.ws

    async def close(self):
        self.closed = True


class Transport(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sessions = []
        self.now = NOW
        self.reject = False
        def factory(**kwargs):
            session = FakeSession(**kwargs)
            session.reject = self.reject
            self.sessions.append(session)
            return session
        self.feed = direct.PocketFeed(clock=lambda:self.now, session_factory=factory)

    async def asyncTearDown(self):
        await self.feed.close()

    async def test_catalogue_browsing_does_not_wait_for_another_candle_request(self):
        catalogue = await self.feed.assets()
        self.sessions[0].hold = True
        task = asyncio.create_task(self.feed.candles(ASSET.key))
        try:
            while self.feed._pending is None:
                await asyncio.sleep(0)
            self.assertTrue(self.feed._lock.locked())
            self.assertIs(await asyncio.wait_for(self.feed.assets(), timeout=0.5), catalogue)
            self.assertFalse(task.done())
            self.assertEqual(len(self.sessions), 1)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_expired_catalogue_still_reconnects(self):
        await self.feed.assets()
        first = self.sessions[0]
        self.now += 61
        await self.feed.assets()
        self.assertTrue(first.closed)
        self.assertEqual(len(self.sessions), 2)

    async def test_cached_catalogue_never_bypasses_authentication(self):
        await self.feed.assets()
        self.feed._authenticated = False
        self.reject = True
        with self.assertRaisesRegex(data.FeedError, '^configuration$'):
            await self.feed.assets()

    async def test_concurrent_taps_share_socket_and_cache_with_no_paid_requests(self):
        with patch('httpx.AsyncClient', side_effect=AssertionError('Paid feed used')):
            first, second = await asyncio.gather(self.feed.candles(ASSET.key), self.feed.candles(ASSET.key))
        self.assertIs(first, second)
        self.assertEqual(len(self.sessions), 1)
        session = self.sessions[0]
        self.assertEqual(session.url, direct.ENDPOINT)
        self.assertFalse(session.kwargs['trust_env'])
        self.assertNotEqual(session.ws_kwargs.get('ssl'), False)
        events = [json.loads(x[2:])[0] for x in session.ws.frames if x.startswith('42')]
        self.assertEqual(events, ['auth', 'changeSymbol'])
        with self.assertRaises(data.FeedError):
            await self.feed._send('buy', {})
        self.now += 16
        await self.feed.candles(ASSET.key)
        self.assertEqual(len([x for x in session.ws.frames if 'changeSymbol' in x]), 2)

    async def test_guest_reconnect_catalogue_and_cancellation_close_old_socket(self):
        await self.feed.assets()
        old = self.sessions[0]
        old.hold = True
        task = asyncio.create_task(self.feed.candles(ASSET.key))
        while self.feed._pending is None:
            await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(old.closed)
        self.assertTrue(old.ws.closed)
        snapshot = await self.feed.candles(ASSET.key)
        self.assertEqual(snapshot.asset.symbol, ASSET.symbol)
        self.assertEqual(len(self.sessions), 2)

    async def test_m5_transport_fetches_m1_tail_and_keeps_requested_period(self):
        snapshot = await self.feed.candles(ASSET.key, 300)
        self.assertEqual(snapshot.period, 300)
        requests = [json.loads(x[2:])[1]['period'] for x in self.sessions[0].ws.frames if 'changeSymbol' in x]
        self.assertEqual(requests, [300, 60])

    async def test_timeout_closes_socket_before_another_request(self):
        await self.feed.assets()
        old = self.sessions[0]
        old.hold = True
        task = asyncio.create_task(self.feed.candles(ASSET.key))
        while self.feed._pending is None:
            await asyncio.sleep(0)
        self.feed._pending[2].set_exception(TimeoutError())
        with self.assertRaisesRegex(data.FeedError, '^service$'):
            await task
        self.assertTrue(old.ws.closed)
        self.assertIsNone(self.feed._pending)

    async def test_auth_rejection_never_trusts_catalogue_and_has_backoff(self):
        self.reject = True
        for _ in range(2):
            with self.assertRaisesRegex(data.FeedError, '^configuration$'):
                await self.feed.assets()
        self.assertEqual(len(self.sessions), 1)
        self.assertTrue(self.sessions[0].closed)

    async def test_invalid_payload_drops_connection_and_blocks_retry_burst(self):
        await self.feed.assets()
        self.sessions[0].payload['candles'][1][2] = 1.16
        for _ in range(2):
            with self.assertRaisesRegex(data.FeedError, '^invalid$'):
                await self.feed.candles(ASSET.key)
        self.assertEqual(len(self.sessions), 1)
        self.assertTrue(self.sessions[0].closed)

    async def test_closed_missing_or_invalid_selection_never_requests_candles(self):
        await self.feed.assets()
        self.feed._catalog = (data.Asset(ASSET.symbol, ASSET.name, False, None),)
        for key, period, code in [(ASSET.key, 60, 'closed'), ('missing', 60, 'asset'), (ASSET.key, 5, 'invalid')]:
            with self.assertRaisesRegex(data.FeedError, '^'+code+'$'):
                await self.feed.candles(key, period)
        self.assertFalse(any('changeSymbol' in x for x in self.sessions[0].ws.frames))

    async def test_provider_factory_never_falls_back_to_otcharts(self):
        with patch.dict(os.environ, MARKET_DATA_PROVIDER='pocketoption', OTCHARTS_API_KEY='unused'), \
                patch.object(data, 'OTCharts', side_effect=AssertionError('Paid fallback')):
            try:
                self.assertIsInstance(data.feed(), direct.PocketFeed)
            finally:
                await data.close()


if __name__ == '__main__':
    unittest.main()
