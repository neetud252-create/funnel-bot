"""Offline feed contract, cache, freshness and chart rendering checks."""
import asyncio
import io
import os
import unittest
from unittest.mock import patch

import httpx
import market_data as data

ASSET = data.Asset('EURUSD_otc', 'EUR/USD OTC', True, None)
NOW = 1792785600 + 12


def payload(period=60, now=NOW, count=101, symbol=ASSET.symbol):
    end = int(now) // period * period
    return dict(symbol=symbol, interval=f'{period}s', candles=[dict(
        time=end - (count-1-i)*period + data.CLOCK_OFFSET,
        open=1.1 + i*.00001, high=1.1003+i*.00001,
        low=1.0997+i*.00001, close=1.1001+i*.00001) for i in range(count)])


class Parsing(unittest.TestCase):
    def test_broker_clock_normalization_and_forming_candle_exclusion(self):
        for period in (60, 300):
            snapshot = data.parse_candles(payload(period), ASSET, period, NOW)
            self.assertEqual(len(snapshot.candles), 100)
            self.assertEqual(snapshot.closed_at, int(NOW)//period*period)
            self.assertEqual(snapshot.candles[-1].close, 1.1001+99*.00001)
            snapshot.require_fresh(NOW)

    def test_rejects_mismatched_identity_timeframe_and_ohlc(self):
        cases = [dict(symbol='GBPUSD_otc'), dict(venue='quotex'), dict(interval='300s')]
        for change in cases:
            value = payload()
            value.update(change)
            with self.assertRaisesRegex(data.FeedError, '^invalid$'):
                data.parse_candles(value, ASSET, 60, NOW)
        for field, bad in [('close', float('nan')), ('open', True), ('low', 2),
                           ('high', -1), ('time', 1700000000.5)]:
            value = payload()
            value['candles'][3][field] = bad
            with self.assertRaisesRegex(data.FeedError, '^invalid$'):
                data.parse_candles(value, ASSET, 60, NOW)

    def test_gaps_stale_short_and_closed_data_fail_without_filling_prices(self):
        value = payload()
        value['candles'].pop(50)
        for supplied, asset, now, code in [
            (value, ASSET, NOW, 'gaps'),
            (payload(), ASSET, NOW+180, 'stale'),
            (payload(count=20), ASSET, NOW, 'history'),
            (dict(payload(), open=False), ASSET, NOW, 'closed'),
            (payload(), data.Asset(ASSET.symbol, ASSET.name, False, None), NOW, 'closed'),
        ]:
            with self.assertRaisesRegex(data.FeedError, '^'+code+'$'):
                data.parse_candles(supplied, asset, 60, now)

    def test_duplicate_candles_cannot_override_the_same_timestamp(self):
        value = payload()
        value['candles'].append(dict(value['candles'][0]))
        self.assertEqual(len(data.parse_candles(value, ASSET, 60, NOW).candles), 100)
        value['candles'][-1]['close'] += .00001
        with self.assertRaisesRegex(data.FeedError, '^invalid$'):
            data.parse_candles(value, ASSET, 60, NOW)

    def test_catalog_handles_nullable_status_stock_symbols_and_all_asset_pages(self):
        rows = [dict(symbol='#AAPL_otc', name='Apple OTC', open=None, payout=None),
                dict(symbol='EURUSD_otc', name='EUR/USD OTC', open=True, payout=89)]
        assets = data.parse_assets(dict(venue='otc', symbols=rows))
        self.assertIsNone(assets[0].payout)
        self.assertIsNone(assets[0].open)
        self.assertEqual(assets[1].key, ASSET.key)
        with self.assertRaisesRegex(data.FeedError, '^invalid$'):
            data.parse_assets(dict(symbols=[dict(symbol='EURUSD')]))

    def test_public_access_requires_both_key_and_explicit_flag(self):
        with patch.dict(os.environ, OTCHARTS_API_KEY='', MARKET_DATA_PUBLIC_ENABLED='true'):
            self.assertFalse(data.public_enabled())
        with patch.dict(os.environ, OTCHARTS_API_KEY='test', MARKET_DATA_PUBLIC_ENABLED='false'):
            self.assertFalse(data.public_enabled())
        with patch.dict(os.environ, OTCHARTS_API_KEY='test', MARKET_DATA_PUBLIC_ENABLED='true'):
            self.assertTrue(data.public_enabled())

    def test_renderer_produces_legible_telegram_png(self):
        from PIL import Image
        import market_chart
        snapshot = data.parse_candles(payload(), ASSET, 60, NOW)
        png = market_chart.render(snapshot, preview=True)
        image = Image.open(io.BytesIO(png))
        self.assertEqual(image.size, (1440, 840))
        self.assertEqual(image.format, 'PNG')
        self.assertLess(len(png), 2_000_000)


class HttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_same_asset_uses_one_candle_request_and_key_only_in_header(self):
        seen = []
        clock = [NOW]
        async def handler(req):
            seen.append(req)
            await asyncio.sleep(0)
            if req.url.path == '/v1/symbols':
                return httpx.Response(200, json=dict(venue='otc', symbols=[dict(symbol=ASSET.symbol, name=ASSET.name)]))
            return httpx.Response(200, json=payload(now=clock[0]))
        feed = data.OTCharts(api_key='PRIVATE_KEY', transport=httpx.MockTransport(handler), clock=lambda: clock[0])
        try:
            first, second = await asyncio.gather(feed.candles(ASSET.key), feed.candles(ASSET.key))
            self.assertIs(first, second)
            self.assertEqual(len(seen), 2)
            for req in seen:
                self.assertEqual(req.method, 'GET')
                self.assertEqual(req.url.host, 'otcharts.com')
                self.assertNotIn('PRIVATE_KEY', str(req.url))
                self.assertEqual(req.headers['authorization'], 'Bearer PRIVATE_KEY')
            self.assertEqual(seen[-1].url.params['tf'], '60')
            clock[0] += 16
            await feed.candles(ASSET.key)
            self.assertEqual(len(seen), 3)
        finally:
            await feed.close()

    async def test_refusals_do_not_leak_secrets_and_quota_backoff_stops_repeated_requests(self):
        for status, code in ((401, 'configuration'), (403, 'plan'), (404, 'asset'), (429, 'quota'), (500, 'service')):
            seen = []
            def handler(req):
                seen.append(req)
                return httpx.Response(status, text='PRIVATE_RESPONSE', headers={'Retry-After': '120'})
            feed = data.OTCharts(api_key='PRIVATE_KEY', transport=httpx.MockTransport(handler), clock=lambda: NOW)
            try:
                for _ in range(2):
                    with self.assertRaisesRegex(data.FeedError, '^'+code+'$'):
                        await feed.assets()
                self.assertEqual(len(seen), 2 if status == 404 else 1)
            finally:
                await feed.close()

    async def test_timeout_and_oversized_response_are_rejected(self):
        def timeout(req):
            raise httpx.ReadTimeout('PRIVATE_TIMEOUT')
        for handler, code in ((timeout, 'service'),
                              (lambda req: httpx.Response(200, content=b'x'*(data.MAX_BODY+1)), 'invalid')):
            feed = data.OTCharts(api_key='test', transport=httpx.MockTransport(handler))
            try:
                with self.assertRaisesRegex(data.FeedError, '^'+code+'$'):
                    await feed.assets()
            finally:
                await feed.close()


if __name__ == '__main__':
    unittest.main()
