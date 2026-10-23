"""Read-only feed smoke test; no Telegram messages, AI calls or trades.

OTCHARTS_API_KEY must be in the environment. Optionally pass --key-file for
an ignored local secret file. Never prints the key or upstream error bodies.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from market_data import OTCharts, FeedError
from market_chart import render


async def main(args):
    if args.key_file:
        os.environ['OTCHARTS_API_KEY'] = Path(args.key_file).read_text().strip()
    feed = OTCharts()
    try:
        usage = await feed.usage()
        print(json.dumps({'plan': usage.get('plan'), 'books': usage.get('books'),
                          'requests': usage.get('requests')}), flush=True)
        assets = await feed.assets()
        print(json.dumps({'assets': len(assets), 'symbols': [a.symbol for a in assets]}), flush=True)
        asset = next((a for a in assets if a.symbol == 'EURUSD_otc' and a.open is not False),
                     next((a for a in assets if a.open is not False), None))
        if asset is None:
            raise FeedError('closed')
        for period in (60, 300):
            snapshot = await feed.candles(asset.key, period)
            output = Path(args.output) / f'market-live-{period}.png'
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(render(snapshot))
            print(json.dumps({'symbol': asset.symbol, 'period': period, 'bars': len(snapshot.candles),
                'closed_at': datetime.fromtimestamp(snapshot.closed_at, timezone.utc).isoformat(),
                'close': snapshot.candles[-1].close, 'chart': str(output.resolve())}), flush=True)
    except FeedError as exc:
        print('Feed check failed: ' + str(exc), flush=True)
        return 1
    finally:
        await feed.close()
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--key-file')
    parser.add_argument('--output', default='test-results')
    sys.exit(asyncio.run(main(parser.parse_args())))
