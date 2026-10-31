"""Numerical indicator and chart contracts, including history and flat markets."""
import io
import types
import unittest

from PIL import Image
import market_chart
import market_data
import strategy_indicators as indicators
from test_market_data import ASSET, NOW, payload

SPECS = [{'kind': 'EMA', 'period': 50}, {'kind': 'RSI', 'period': 14}]


class IndicatorTests(unittest.TestCase):
    def test_ema_sma_seed_and_no_future_data(self):
        self.assertEqual(indicators.ema([1, 2, 3, 4, 5], 3), [None, None, 2, 3, 4])
        self.assertEqual(indicators.ema([1, 2], 3), [None, None])
        prefix = indicators.ema([1, 2, 3, 4], 3)
        self.assertEqual(indicators.ema([1, 2, 3, 4, 1000], 3)[:4], prefix)

    def test_wilder_rsi_seed_smoothing_and_extremes(self):
        # First three changes +1, -1, +2 -> avg gain=1, avg loss=1/3 -> RSI=75.
        values = indicators.rsi([10, 11, 10, 12, 11], 3)
        self.assertEqual(values[:3], [None, None, None])
        self.assertAlmostEqual(values[3], 75)
        self.assertAlmostEqual(values[4], 600 / 11)
        self.assertEqual(indicators.rsi([1, 2, 3, 4], 3)[-1], 100)
        self.assertEqual(indicators.rsi([4, 3, 2, 1], 3)[-1], 0)
        self.assertEqual(indicators.rsi([4, 4, 4, 4], 3)[-1], 50)
        self.assertIsNone(indicators.rsi([1, 2, 3], 3)[-1])

    def test_missing_history_fails_and_input_bars_are_not_changed(self):
        bars = [types.SimpleNamespace(close=float(i)) for i in range(1, 30)]
        before = [bar.close for bar in bars]
        with self.assertRaisesRegex(market_data.FeedError, '^history$'):
            indicators.calculate(bars, SPECS)
        self.assertEqual([bar.close for bar in bars], before)

    def test_chart_renders_requested_indicators_and_context_with_exact_periods(self):
        snapshot = market_data.parse_candles(payload(), ASSET, 60, NOW)
        values = indicators.calculate(snapshot.candles, SPECS)
        self.assertEqual([(x['kind'], x['period']) for x in values], [('EMA', 50), ('RSI', 14)])
        context = indicators.context(snapshot.candles, SPECS)
        self.assertIn('EMA(50)=', context)
        self.assertIn('RSI(14)=', context)
        picture = market_chart.render(snapshot, indicators=SPECS, preview=True)
        image = Image.open(io.BytesIO(picture))
        self.assertEqual(image.size, (1440, 840))
        self.assertLess(len(picture), 2_000_000)


if __name__ == '__main__':
    unittest.main()
