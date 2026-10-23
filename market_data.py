"""Read-only market feeds. No order API or automatic paid-provider fallback.

Contract: https://otcharts.com/api (checked 2026-10-24).
The otc book timestamps are broker-clock seconds, UTC + 2 hours.
"""
import asyncio
from dataclasses import dataclass
import hashlib
import math
import os
import re
import time

import httpx

BASE_URL = 'https://otcharts.com'
PERIODS = (60, 300)
MIN_BARS = 60
CLOCK_OFFSET = 7200
MAX_BODY = 2 * 1024 * 1024


class FeedError(Exception):
    """A fixed local error code; never expose the provider's response or key."""


def provider():
    return os.getenv('MARKET_DATA_PROVIDER', 'otcharts').strip().lower()


def configured():
    if provider() == 'pocketoption':
        return True  # Public guest demo connection; no subscription or credentials.
    return provider() == 'otcharts' and bool(os.getenv('OTCHARTS_API_KEY', '').strip())


def public_enabled():
    return configured() and os.getenv('MARKET_DATA_PUBLIC_ENABLED', '').lower() == 'true'


def enabled(tg_id):
    import config
    return configured() and (public_enabled() or tg_id in config.ADMIN_IDS)


@dataclass(frozen=True)
class Asset:
    symbol: str
    name: str
    open: bool | None
    payout: float | None
    category: str = 'other'

    @property
    def market(self):
        return 'otc' if self.symbol.endswith('_otc') else 'fin'

    @property
    def key(self):
        # Stable across catalog sorting, new listings, restarts and delistings.
        return hashlib.sha256(self.symbol.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Candle:
    time: int
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Snapshot:
    asset: Asset
    period: int
    candles: tuple[Candle, ...]
    source: str = 'OTCharts / Pocket Option OTC'

    @property
    def closed_at(self):
        return self.candles[-1].time + self.period

    def require_fresh(self, now=None):
        now = time.time() if now is None else now
        # Latest completed candle should be the previous interval. Allow 30s
        # for upstream ingestion; never silently analyse an old cached window.
        if self.closed_at > now or now - self.closed_at > self.period + 30:
            raise FeedError('stale')


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise FeedError('invalid')
    if not math.isfinite(value) or value <= 0:
        raise FeedError('invalid')
    return float(value)


def parse_assets(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('symbols'), list):
        raise FeedError('invalid')
    if payload.get('venue', 'otc') != 'otc':
        raise FeedError('invalid')
    found = {}
    for row in payload['symbols']:
        if not isinstance(row, dict):
            raise FeedError('invalid')
        symbol = row.get('symbol')
        if not isinstance(symbol, str) or not re.fullmatch(r'[#A-Za-z0-9_.-]{1,64}_otc', symbol):
            raise FeedError('invalid')
        name = row.get('name', symbol)
        if not isinstance(name, str) or not name or len(name) > 120:
            raise FeedError('invalid')
        is_open = row.get('open')
        if is_open is not None and type(is_open) is not bool:
            raise FeedError('invalid')
        payout = row.get('payout')
        if payout is not None:
            if isinstance(payout, bool) or not isinstance(payout, (float, int)) or not math.isfinite(payout) or not 0 <= payout <= 100:
                raise FeedError('invalid')
        found[symbol] = Asset(symbol, name, is_open, payout)
    if not found:
        raise FeedError('empty')
    return tuple(sorted(found.values(), key=lambda item: item.name.casefold()))


def parse_candles(payload, asset, period, now=None):
    now = time.time() if now is None else now
    if not isinstance(payload, dict) or not isinstance(payload.get('candles'), list):
        raise FeedError('invalid')
    if payload.get('symbol') != asset.symbol or payload.get('venue', 'otc') != 'otc':
        raise FeedError('invalid')
    if period not in PERIODS or payload.get('interval') != f'{period}s':
        raise FeedError('invalid')
    if payload.get('open') is False or asset.open is False:
        raise FeedError('closed')
    return validated_candles(payload['candles'], asset, period, now,
                             offset=CLOCK_OFFSET, source='OTCharts / Pocket Option OTC')


def validated_candles(rows, asset, period, now, *, offset, source):
    """Shared OHLC checks; provider clock conversion must be explicit."""
    if period not in PERIODS or not isinstance(rows, list) or len(rows) > 10000:
        raise FeedError('invalid')
    if asset.open is False:
        raise FeedError('closed')
    found = {}
    for row in rows:
        if not isinstance(row, dict):
            raise FeedError('invalid')
        stamp = _number(row.get('time'))
        if stamp != int(stamp):
            raise FeedError('invalid')
        stamp = int(stamp) - offset
        if stamp % period or stamp > now + period:
            raise FeedError('invalid')
        o, h, l, c = (_number(row.get(key)) for key in ('open', 'high', 'low', 'close'))
        if not l <= min(o, c) <= max(o, c) <= h:
            raise FeedError('invalid')
        candle = Candle(stamp, o, h, l, c)
        if stamp in found and found[stamp] != candle:
            raise FeedError('invalid')
        if stamp + period <= now:
            found[stamp] = candle
    bars = tuple(found[key] for key in sorted(found))[-100:]
    if len(bars) < MIN_BARS:
        raise FeedError('history')
    # Fail closed on gaps, including internal gaps even when the API omits its
    # optional gaps summary. Do not forward-fill invented candles.
    if any(b.time - a.time != period for a, b in zip(bars, bars[1:])):
        raise FeedError('gaps')
    snapshot = Snapshot(asset, period, bars, source)
    snapshot.require_fresh(now)
    return snapshot


class OTCharts:
    def __init__(self, *, api_key=None, transport=None, clock=time.time):
        self._key = api_key
        self._transport = transport
        self._clock = clock
        self._client = None
        self._catalog = None
        self._catalog_until = 0
        self._cache = {}
        self._lock = asyncio.Lock()
        self._blocked_until = 0
        self._blocked_code = 'service'

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _get(self, path, params=None):
        key = self._key or os.getenv('OTCHARTS_API_KEY', '').strip()
        if not key:
            raise FeedError('configuration')
        if self._clock() < self._blocked_until:
            raise FeedError(self._blocked_code)
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=BASE_URL, timeout=20,
                follow_redirects=False, transport=self._transport,
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=2))
        try:
            async with self._client.stream('GET', path, params=params,
                    headers={'Authorization': f'Bearer {key}', 'Accept': 'application/json'}) as response:
                if response.status_code != 200:
                    code = {401: 'configuration', 403: 'plan', 404: 'asset', 429: 'quota'}.get(response.status_code, 'service')
                    if code in ('configuration', 'plan', 'quota', 'service'):
                        retry = response.headers.get('retry-after', '')
                        delay = min(3600, max(60, int(retry))) if retry.isdigit() else 60
                        self._blocked_until = self._clock() + delay
                        self._blocked_code = code
                    raise FeedError(code)
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_BODY:
                        raise FeedError('invalid')
                import json
                return json.loads(data)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise FeedError('service') from None

    async def assets(self):
        async with self._lock:
            if self._catalog is None or self._clock() >= self._catalog_until:
                self._catalog = parse_assets(await self._get('/v1/symbols', {'venue': 'otc'}))
                self._catalog_until = self._clock() + 300
            return self._catalog

    async def resolve(self, asset_key):
        for asset in await self.assets():
            if asset.key == asset_key:
                return asset
        raise FeedError('asset')

    async def candles(self, asset_key, period=60):
        if period not in PERIODS:
            raise FeedError('invalid')
        asset = await self.resolve(asset_key)
        if asset.open is False:
            raise FeedError('closed')
        # Shared demand-driven cache. No timer polls, per-user streams or
        # prefetching the whole catalog. Concurrent identical taps reuse data.
        async with self._lock:
            key = (asset.symbol, period)
            cached = self._cache.get(key)
            if cached and self._clock() < cached[0]:
                cached[1].require_fresh(self._clock())
                return cached[1]
            payload = await self._get('/v1/candles', dict(venue='otc', symbol=asset.symbol,
                                                        tf=period, limit=101))
            snapshot = parse_candles(payload, asset, period, self._clock())
            if len(self._cache) >= 512:
                self._cache.pop(next(iter(self._cache)))
            # Invalidate when another candle closes, with a 15-second ceiling.
            expiry = min(self._clock() + 15, snapshot.closed_at + period)
            self._cache[key] = (expiry, snapshot)
            return snapshot

    async def usage(self):
        return await self._get('/v1/usage')


_feeds = {}


def feed():
    loop = asyncio.get_running_loop()
    if loop not in _feeds:
        if provider() == 'pocketoption':
            from pocket_feed import PocketFeed
            _feeds[loop] = PocketFeed()
        elif provider() == 'otcharts':
            _feeds[loop] = OTCharts()
        else:
            raise FeedError('configuration')
    return _feeds[loop]


async def close():
    instance = _feeds.pop(asyncio.get_running_loop(), None)
    if instance:
        await instance.close()


async def startup_check():
    """One bounded read-only deployment check; no Telegram, AI or trade calls."""
    if provider() != 'pocketoption':
        return
    import logging
    async def check():
        assets = await feed().assets()
        asset = next((a for a in assets if a.symbol == 'EURUSD_otc' and a.open),
                     next((a for a in assets if a.open), None))
        if asset is None:
            raise FeedError('closed')
        snapshot = await feed().candles(asset.key, 60)
        logging.info('Direct market feed verified; assets=%d; open=%d; sample=%s; bars=%d; candle_age=%.1fs',
                     len(assets), sum(bool(a.open) for a in assets), asset.symbol,
                     len(snapshot.candles), time.time()-snapshot.closed_at)
    try:
        await asyncio.wait_for(check(), timeout=40)
    except Exception as exc:
        logging.warning('Direct market feed check failed; code=%s',
                        str(exc) if isinstance(exc, FeedError) else 'service')
