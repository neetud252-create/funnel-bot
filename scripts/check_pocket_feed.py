"""Read-only direct demo-feed probe: no Telegram, AI, trades or credentials."""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pocket_feed import PocketFeed
from market_data import FeedError
import market_catalog


async def main(args):
    feed = PocketFeed()
    report = {'source': 'Pocket Option guest demo', 'checks': []}
    try:
        assets = await feed.assets()
        entries = market_catalog.entries(assets)
        matched = [e for e in entries if e.key in {s.key for s in market_catalog.SEED} and e.live]
        report.update(checked_at=datetime.now(timezone.utc).isoformat(),
                      assets=len(assets), matched_seed=len(matched),
                      open=sum(a.open for a in assets),
                      categories=dict(Counter(a.category for a in assets)),
                      missing_seed=[s.name for s in market_catalog.SEED if not any(e.key == s.key for e in matched)])
        print(json.dumps({k:v for k,v in report.items() if k != 'checks'}), flush=True)
        selected = list(assets) if args.all else []
        if not args.all:
            for category in market_catalog.CATEGORIES:
                item = next((a for a in assets if a.category == category and a.open), None)
                if item:
                    selected.append(item)
        for asset in selected:
            if not asset.open:
                continue
            try:
                started = time.monotonic()
                snapshot = await feed.candles(asset.key, args.period)
                check = dict(symbol=asset.symbol, period=args.period, bars=len(snapshot.candles),
                             closed_at=snapshot.closed_at, age_seconds=round(time.time()-snapshot.closed_at, 1),
                             seconds=round(time.monotonic()-started, 2), status='ok')
                if args.chart and not any(c.get('chart') for c in report['checks']):
                    from market_chart import render
                    path = Path(args.output).with_suffix('.png')
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(render(snapshot))
                    check['chart'] = str(path.resolve())
            except FeedError as exc:
                check = dict(symbol=asset.symbol, status=str(exc))
                # Respect connection backoff when auditing the full catalogue.
                await asyncio.sleep(5)
            report['checks'].append(check)
            print(json.dumps(check), flush=True)
        # Verify reconnect works independently of the first guest session.
        await feed.close()
        report['reconnect_assets'] = len(await feed.assets())
        print(json.dumps({'reconnect_assets': report['reconnect_assets']}), flush=True)
    except FeedError as exc:
        report['error'] = str(exc)
        print(json.dumps({'error': str(exc)}), flush=True)
    finally:
        await feed.close()
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    return int('error' in report or not report['checks'] or any(c['status'] != 'ok' for c in report['checks']))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--all', action='store_true', help='Check each currently open asset, sequentially.')
    parser.add_argument('--chart', action='store_true')
    parser.add_argument('--period', type=int, choices=(60, 300), default=60)
    parser.add_argument('--output', default='test-results/direct-feed.json')
    sys.exit(asyncio.run(main(parser.parse_args())))
