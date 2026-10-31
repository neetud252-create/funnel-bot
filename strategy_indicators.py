"""Close-price EMA and Wilder RSI for imported strategies, using closed bars only.

Definitions: https://www.tradingview.com/support/solutions/43000592270-exponential-moving-average/
https://www.tradingview.com/support/solutions/43000502338-relative-strength-index-rsi/
EMA starts with an SMA seed; RSI starts with the mean of its first n changes.
Unavailable warm-up values stay None. No future bars or fabricated history.
"""
import math

from market_data import FeedError


def ema(closes, period):
    values = [None] * len(closes)
    if len(closes) < period:
        return values
    current = sum(closes[:period]) / period
    values[period - 1] = current
    alpha = 2 / (period + 1)
    for index in range(period, len(closes)):
        current += alpha * (closes[index] - current)
        values[index] = current
    return values


def rsi(closes, period):
    values = [None] * len(closes)
    if len(closes) <= period:
        return values
    changes = [b - a for a, b in zip(closes, closes[1:])]
    gain = sum(max(change, 0) for change in changes[:period]) / period
    loss = sum(max(-change, 0) for change in changes[:period]) / period

    def value():
        # A flat window has no directional momentum; never treat it as BUY.
        if gain == loss == 0:
            return 50.0
        return 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)

    values[period] = value()
    for index in range(period + 1, len(closes)):
        change = changes[index - 1]
        gain = (gain * (period - 1) + max(change, 0)) / period
        loss = (loss * (period - 1) + max(-change, 0)) / period
        values[index] = value()
    return values


def calculate(candles, specs):
    closes = [bar.close for bar in candles]
    if not closes or any(not math.isfinite(value) or value <= 0 for value in closes):
        raise FeedError('history')
    result = []
    for spec in specs:
        kind, period = spec['kind'], spec['period']
        if kind not in ('EMA', 'RSI') or type(period) is not int or not 2 <= period <= 500:
            raise FeedError('invalid')
        values = (ema if kind == 'EMA' else rsi)(closes, period)
        if values[-1] is None:
            raise FeedError('history')
        result.append({'kind': kind, 'period': period, 'values': values})
    return result


def context(candles, specs):
    if not specs:
        return ''
    values = calculate(candles, specs)
    return ('Indicators calculated from completed candle closes, EMA SMA-seeded, RSI Wilder: ' +
            ', '.join(f"{item['kind']}({item['period']})={item['values'][-1]:.8g}" for item in values) + '. ')
