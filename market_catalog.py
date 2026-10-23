"""Browsable broker catalogue, independent of candle-feed plan permissions.

Names come from Pocket Option's published schedules (pocket_assets.json).
An entry is executable only when it matches an asset returned by our feed.
FIN prices are never substituted with OTC or external spot-FX prices.
"""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

import config

CATEGORIES = {
    'forex': ('💱 Currency pairs', 'Select a currency pair', 'currency_pair'),
    'crypto': ('🪙 Cryptocurrencies', 'Select a cryptocurrency', 'asset_category'),
    'stocks': ('📈 Stocks', 'Select a stock', 'asset_category'),
    'indices': ('📊 Indices', 'Select an index', 'asset_category'),
    'commodities': ('🛢 Commodities', 'Select a commodity', 'asset_category'),
}


def normalized(name):
    return re.sub(r'[^a-z0-9]', '', name.casefold())


@dataclass(frozen=True)
class Entry:
    name: str
    category: str
    market: str
    live: object = None

    @property
    def key(self):
        return hashlib.sha256((self.market + ':' + normalized(self.name)).encode()).hexdigest()[:16]


_SOURCE = json.loads(Path(__file__).with_name('pocket_assets.json').read_text(encoding='utf-8'))
_ORDER = {normalized(name): index for index, name in enumerate(config.PAIRS_RAW)}
SEED = tuple(sorted((Entry(**row) for row in _SOURCE['assets']),
                    key=lambda e: (_ORDER.get(normalized(e.name), 999)
                                   if e.market == 'otc' and e.category == 'forex' else 999)))


def entries(live=()):
    # Only normalize punctuation, never invert a pair or conflate OTC and FIN.
    by_name = {normalized(a.name): a for a in live}
    by_symbol = {normalized(a.symbol): a for a in live}
    result, matched = [], set()
    for item in SEED:
        asset = None
        if item.market == 'otc':
            asset = by_name.get(normalized(item.name))
            if asset is None and item.category == 'forex':
                asset = by_symbol.get(normalized(item.name))
        if asset:
            matched.add(asset.key)
        result.append(Entry(item.name, item.category, item.market, asset))
    for asset in live:
        if asset.key not in matched:
            # New provider listings remain reachable without guessing their
            # category or ever constructing a candle-request symbol ourselves.
            result.append(Entry(asset.name, 'other', 'otc', asset))
    return tuple(result)


def find(items, key):
    return next((e for e in items if e.key == key or (e.live and e.live.key == key)), None)


def category_info(category):
    return CATEGORIES.get(category, ('📋 Other assets', 'Select an asset', 'asset_category'))
