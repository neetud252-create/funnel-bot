"""Opt-in deployed import check: no Telegram messages, user edits or orders.

Run with one public video ID. Uses the service's configured keys on the server.
Only bounded public-video source cache writes and bounded model requests.
"""
import asyncio
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chart_analysis as api
import youtube_source
import youtube_strategy


async def check_signal(saved):
    import market_data
    import market_chart
    import market_signals
    import strategy_indicators
    try:
        feed = market_data.feed()
        assets = [asset for asset in await feed.assets() if asset.open is not False]
        assets.sort(key=lambda asset: (asset.symbol != 'EURUSD_otc', asset.symbol))
        snapshot = None
        for asset in assets[:3]:
            try:
                snapshot = await feed.candles(asset.key, 60)
                break
            except market_data.FeedError as exc:
                logging.info('YOUTUBE_SIGNAL_CHECK feed unavailable: code=%s', str(exc))
        if snapshot is None:
            raise api.AnalysisError('service')
        indicators = saved['rules'].get('indicators', [])
        png = await asyncio.to_thread(market_chart.render, snapshot, indicators=indicators)
        context = (strategy_indicators.context(snapshot.candles, indicators) +
                   f'Asset: {snapshot.asset.symbol}. Candle timeframe: 60 seconds. '
                   f'All candles shown are completed OHLC bars from {snapshot.source}. '
                   f'Latest close: {snapshot.candles[-1].close:.8g}; UTC close time: {snapshot.closed_at}.')
        result = await api.analyse(png, 'en', context, strategy=saved)
        snapshot.require_fresh()
        caption = market_signals.result_text(snapshot, result, strategy=saved)
        assert result['direction'] in ('BUY', 'SELL')
        assert '<b>' + result['direction'] + '</b>' in caption
        print('YOUTUBE_SIGNAL_CHECK', {'ok': True, 'direction': result['direction'],
              'entry_confirmed': result['entry_confirmed'], 'bars': len(snapshot.candles)}, flush=True)
    finally:
        await market_data.close()


async def main(video, *, signal=False, saved_strategy=False):
    import asyncpg
    import db
    pool = await asyncpg.create_pool(db.DB_URL, min_size=1, max_size=1)
    try:
        async with pool.acquire() as conn:
            await conn.execute(youtube_source.SCHEMA)
        if saved_strategy:
            async with pool.acquire() as conn:
                value = await conn.fetchval('''SELECT chart_strategy FROM users
                    WHERE chart_strategy->>'video_id'=$1 LIMIT 1''', video)
            saved = youtube_strategy.decode_saved(value)
            if saved is None:
                raise api.AnalysisError('strategy_saved')
            origin, characters = 'saved_strategy', None
        else:
            text, origin = await youtube_source.load(video, 'en', pool=pool)
            saved = await youtube_strategy.generate(text, video, 'en', origin=origin)
            characters = len(text)
        rules = saved['rules']
        if video == 'JXGAW2LXAaA':
            assert rules['buy_rules'] and rules['sell_rules']
            assert {('EMA', 50), ('RSI', 14)}.issubset(
                {(item['kind'], item['period']) for item in rules['indicators']})
        print('YOUTUBE_STRATEGY_CHECK' if saved_strategy else 'YOUTUBE_IMPORT_CHECK', {
            'ok': True, 'origin': origin, 'characters': characters,
            'buy_rules': len(rules['buy_rules']), 'sell_rules': len(rules['sell_rules']),
            'indicators': [(item['kind'], item['period']) for item in rules['indicators']],
        }, flush=True)
        if signal:
            await check_signal(saved)
    finally:
        await pool.close()
        await api.close_provider_client()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('video')
    parser.add_argument('--signal', action='store_true', help='Also analyse a real live chart without sending it')
    parser.add_argument('--saved-strategy', action='store_true', help='Read an existing active strategy for this video')
    args = parser.parse_args()
    if not youtube_strategy.VIDEO_ID.fullmatch(args.video):
        raise SystemExit('Provide exactly one public YouTube video ID')
    logging.basicConfig(level=logging.INFO)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    try:
        asyncio.run(main(args.video, signal=args.signal, saved_strategy=args.saved_strategy))
    except Exception as exc:
        print('YOUTUBE_IMPORT_CHECK', {'ok': False, 'type': type(exc).__name__,
              'code': str(exc) if isinstance(exc, api.AnalysisError) else 'failed'}, flush=True)
        raise SystemExit(1)
