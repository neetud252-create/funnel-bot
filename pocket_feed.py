"""Independent read-only Pocket Option guest-demo feed.

Uses only guest auth and changeSymbol (market history), with verified TLS.
No account SSID, third-party broker library, order method, or paid fallback.
Wire shapes verified against the guest endpoint on 2026-10-24. See
MARKET_SIGNALS.md for the protocol references and operational limitations.
"""
import asyncio
from collections import defaultdict
import contextlib
import json
import re
import secrets
import string
import time

import aiohttp

from market_data import Asset, FeedError, PERIODS, _number, validated_candles

ENDPOINT = 'wss://try-demo-eu.po.market/socket.io/?EIO=4&transport=websocket'
ORIGIN = 'https://pocketoption.com'
SOURCE = 'Pocket Option demo feed'
BROKER_OFFSET = 7200
MAX_BODY = 2 * 1024 * 1024
CATEGORIES = {'currency': 'forex', 'cryptocurrency': 'crypto', 'stock': 'stocks',
              'index': 'indices', 'commodity': 'commodities'}


def parse_assets(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 2000:
        raise FeedError('invalid')
    found = {}
    for row in rows:
        if not isinstance(row, list) or len(row) < 16:
            raise FeedError('invalid')
        symbol, name, kind, payout, opened = row[1], row[2], row[3], row[5], row[14]
        if (not isinstance(symbol, str) or not re.fullmatch(r'[#A-Za-z0-9_.-]{1,64}', symbol)
                or not isinstance(name, str) or not 1 <= len(name) <= 120
                or type(opened) is not bool or not isinstance(kind, str)):
            raise FeedError('invalid')
        if isinstance(payout, bool) or not isinstance(payout, (int, float)) or not 0 <= payout <= 100:
            raise FeedError('invalid')
        asset = Asset(symbol, name, opened, float(payout), CATEGORIES.get(kind, 'other'))
        if symbol in found and found[symbol] != asset:
            raise FeedError('invalid')
        found[symbol] = asset
    return tuple(sorted(found.values(), key=lambda a: a.name.casefold()))


def parse_history(payload, asset, period, now, *, minutes=None):
    """Merge completed broker bars with fully covered tick-built recent bars.

    The server's OHLC array trails its tick history by several minutes. Only
    complete tick intervals may extend it; never fill gaps or use a forming bar.
    Overlapping completed bars must agree with the server's own OHLC exactly.
    The newest server bar can be truncated at the history cutoff (notably M5),
    so it is replaced only when a complete tick interval is available.
    """
    if (not isinstance(payload, dict) or payload.get('asset') != asset.symbol
            or type(payload.get('period')) is not int or payload['period'] != period
            or period not in PERIODS):
        raise FeedError('invalid')
    raw_bars, raw_ticks = payload.get('candles'), payload.get('history')
    if (not isinstance(raw_bars, list) or len(raw_bars) > 10000
            or not isinstance(raw_ticks, list) or not 1 <= len(raw_ticks) <= 50000):
        raise FeedError('invalid')
    bars = {}
    for row in raw_bars:
        if not isinstance(row, list) or len(row) != 5:
            raise FeedError('invalid')
        stamp, o, c, h, l = (_number(v) for v in row)
        if (stamp != int(stamp) or stamp % period or stamp-BROKER_OFFSET > now+period
                or not l <= min(o, c) <= max(o, c) <= h):
            raise FeedError('invalid')
        candle = dict(time=int(stamp), open=o, close=c, high=h, low=l)
        if stamp in bars and bars[stamp] != candle:
            raise FeedError('invalid')
        bars[int(stamp)] = candle
    if bars and period == 300:
        bars.pop(max(bars))
    ticks = {}
    for row in raw_ticks:
        if not isinstance(row, list) or len(row) != 2:
            raise FeedError('invalid')
        stamp, price = (_number(v) for v in row)
        if stamp in ticks and ticks[stamp] != price:
            raise FeedError('invalid')
        ticks[stamp] = price
    # Validate the documented broker clock against a live sample. Do not infer
    # an offset from stale data, which could make an old series appear current.
    age = now - (max(ticks) - BROKER_OFFSET)
    if not -5 <= age <= 30:
        raise FeedError('stale')
    groups = defaultdict(list)
    for stamp in sorted(ticks):
        groups[int(stamp) // period * period].append((stamp, ticks[stamp]))
    for start, group in groups.items():
        end = start + period
        if end - BROKER_OFFSET > now or start <= min(ticks) or end > max(ticks):
            continue
        # A truncated first/last interval or an interrupted tick stream is not
        # a completed candle. Preserve a server-provided bar if one exists.
        if (group[0][0] - start > 2 or end - group[-1][0] > 2
                or any(b[0] - a[0] > 5 for a, b in zip(group, group[1:]))):
            continue
        prices = [price for _, price in group]
        candle = dict(time=start, open=prices[0], close=prices[-1], high=max(prices), low=min(prices))
        if start in bars and bars[start] != candle:
            raise FeedError('invalid')
        bars[start] = candle
    if minutes is not None:
        if (period != 300 or minutes.period != 60 or minutes.asset.symbol != asset.symbol
                or minutes.source != SOURCE):
            raise FeedError('invalid')
        minutes.require_fresh(now)
        five_minutes = defaultdict(list)
        for bar in minutes.candles:
            five_minutes[bar.time // period * period].append(bar)
        for start, group in five_minutes.items():
            if len(group) != 5 or [b.time for b in group] != list(range(start, start+period, 60)):
                continue
            stamp = start + BROKER_OFFSET
            candle = dict(time=stamp, open=group[0].open, close=group[-1].close,
                          high=max(b.high for b in group), low=min(b.low for b in group))
            if stamp in bars and bars[stamp] != candle:
                raise FeedError('invalid')
            bars[stamp] = candle
    return validated_candles(list(bars.values()), asset, period, now,
                             offset=BROKER_OFFSET, source=SOURCE)


class PocketFeed:
    """One bounded, shared, demand-driven guest connection per worker loop."""
    def __init__(self, *, clock=time.time, session_factory=aiohttp.ClientSession):
        self._clock = clock
        self._session_factory = session_factory
        self._lock = asyncio.Lock()
        self._session = self._ws = self._reader = None
        self._ready = asyncio.Event()
        self._authenticated = False
        self._catalog = ()
        self._catalog_at = 0
        self._pending = None
        self._cache = {}
        self._blocked_until = 0
        self._failure = 'service'

    async def _send(self, event, payload):
        if event not in ('auth', 'changeSymbol'):
            raise FeedError('invalid')
        await self._ws.send_str('42' + json.dumps([event, payload], separators=(',', ':')))

    def _event(self, name, payload):
        if name in ('NotAuthorized', 'errorauth'):
            raise FeedError('configuration')
        if name == 'successauth':
            self._authenticated = True
        elif name == 'updateAssets':
            self._catalog = parse_assets(payload)
            self._catalog_at = self._clock()
        elif name == 'updateHistoryNewFast' and self._authenticated and self._pending:
            symbol, period, future = self._pending
            if (isinstance(payload, dict) and payload.get('asset') == symbol
                    and payload.get('period') == period and not future.done()):
                future.set_result(payload)
        if self._authenticated and self._catalog:
            self._ready.set()

    async def _read(self, token):
        attachment = None
        try:
            async for message in self._ws:
                if message.type == aiohttp.WSMsgType.TEXT:
                    raw = message.data
                    if raw.startswith('0{'):
                        await self._ws.send_str('40')
                    elif raw == '40' or raw.startswith('40{'):
                        await self._send('auth', dict(token=token, balance=50000, isFastHistory=True))
                    elif raw == '2':
                        await self._ws.send_str('3')
                    elif raw in ('1', '41'):
                        raise FeedError('service')
                    elif raw.startswith('42'):
                        event = json.loads(raw[2:])
                        self._event(event[0], event[1] if len(event) > 1 else None)
                    elif raw.startswith('451-'):
                        event = json.loads(raw[4:])
                        if (attachment is not None or len(event) != 2 or not isinstance(event[0], str)
                                or event[1] != {'_placeholder': True, 'num': 0}):
                            raise FeedError('invalid')
                        attachment = event[0]
                elif message.type == aiohttp.WSMsgType.BINARY:
                    if attachment is None:
                        raise FeedError('invalid')
                    # Discard balance and other account-shaped payloads without
                    # storing or logging them. Only market data is retained.
                    if attachment in ('successauth', 'updateAssets', 'updateHistoryNewFast', 'NotAuthorized', 'errorauth'):
                        self._event(attachment, json.loads(message.data))
                    attachment = None
                elif message.type == aiohttp.WSMsgType.ERROR:
                    raise FeedError('service')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._failure = str(exc) if isinstance(exc, FeedError) else 'service'
        finally:
            self._authenticated = False
            self._ready.set()
            if self._pending and not self._pending[2].done():
                self._pending[2].set_exception(FeedError(self._failure))

    async def _disconnect(self):
        if self._reader:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
        if self._ws:
            await self._ws.close()
        if self._session:
            await self._session.close()
        self._reader = self._ws = self._session = None
        self._authenticated = False
        self._catalog = ()
        self._cache.clear()

    async def close(self):
        async with self._lock:
            await self._disconnect()

    async def _ensure(self):
        if (self._ws and not self._ws.closed and self._authenticated
                and self._clock() - self._catalog_at < 60):
            return
        if self._clock() < self._blocked_until:
            raise FeedError(self._failure)
        await self._disconnect()
        self._ready.clear()
        self._failure = 'service'
        try:
            self._session = self._session_factory(timeout=aiohttp.ClientTimeout(total=12), trust_env=False)
            # Default TLS certificate and hostname verification remain enabled.
            self._ws = await self._session.ws_connect(ENDPOINT, origin=ORIGIN,
                                                     receive_timeout=35, max_msg_size=MAX_BODY)
            token = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(10))
            self._reader = asyncio.create_task(self._read(token))
            await asyncio.wait_for(self._ready.wait(), timeout=12)
            if not self._authenticated or not self._catalog:
                raise FeedError(self._failure)
        except BaseException as exc:
            await self._disconnect()
            if isinstance(exc, asyncio.CancelledError):
                raise
            self._failure = str(exc) if isinstance(exc, FeedError) else 'service'
            self._blocked_until = self._clock() + 5
            raise FeedError(self._failure) from None

    async def assets(self):
        async with self._lock:
            await self._ensure()
            return self._catalog

    async def _history(self, asset, period):
        future = asyncio.get_running_loop().create_future()
        self._pending = (asset.symbol, period, future)
        try:
            await self._send('changeSymbol', dict(asset=asset.symbol, period=period))
            return await asyncio.wait_for(future, timeout=18)
        finally:
            future.cancel()
            if future.done() and not future.cancelled():
                future.exception()
            self._pending = None

    async def candles(self, asset_key, period=60):
        if type(period) is not int or period not in PERIODS:
            raise FeedError('invalid')
        async with self._lock:
            await self._ensure()
            asset = next((a for a in self._catalog if a.key == asset_key), None)
            if asset is None:
                raise FeedError('asset')
            if not asset.open:
                raise FeedError('closed')
            key = (asset.symbol, period)
            cached = self._cache.get(key)
            if cached and self._clock() < cached[0]:
                cached[1].require_fresh(self._clock())
                return cached[1]
            try:
                payload = await self._history(asset, period)
                minutes = None
                if period == 300:
                    # M5 history may end mid-bar, with too few recent ticks to
                    # reconstruct it. Completed M1 OHLC supplies the exact tail.
                    minute_payload = await self._history(asset, 60)
                    minutes = parse_history(minute_payload, asset, 60, self._clock())
                snapshot = parse_history(payload, asset, period, self._clock(), minutes=minutes)
                if len(self._cache) >= 512:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[key] = (min(self._clock() + 15, snapshot.closed_at + period), snapshot)
                return snapshot
            except BaseException as exc:
                # Closing on a timeout/cancellation prevents a delayed response
                # from an earlier same-symbol request satisfying the next one.
                self._pending = None
                await self._disconnect()
                if isinstance(exc, asyncio.CancelledError):
                    raise
                self._failure = str(exc) if isinstance(exc, FeedError) else 'service'
                self._blocked_until = self._clock() + 5
                raise FeedError(self._failure) from None
            finally:
                self._pending = None
