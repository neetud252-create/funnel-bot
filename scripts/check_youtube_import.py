"""Opt-in deployed import check: no Telegram messages, user edits or orders.

Run with one public video ID. Uses the service's configured keys on the server.
Only bounded public-video source cache writes and bounded model requests.
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chart_analysis as api
import youtube_source
import youtube_strategy


async def main(video):
    import asyncpg
    import db
    pool = await asyncpg.create_pool(db.DB_URL, min_size=1, max_size=1)
    try:
        async with pool.acquire() as conn:
            await conn.execute(youtube_source.SCHEMA)
        text, origin = await youtube_source.load(video, 'en', pool=pool)
        saved = await youtube_strategy.generate(text, video, 'en', origin=origin)
        rules = saved['rules']
        if video == 'JXGAW2LXAaA':
            assert rules['buy_rules'] and rules['sell_rules']
            assert {('EMA', 50), ('RSI', 14)}.issubset(
                {(item['kind'], item['period']) for item in rules['indicators']})
        print('YOUTUBE_IMPORT_CHECK', {
            'ok': True, 'origin': origin, 'characters': len(text),
            'buy_rules': len(rules['buy_rules']), 'sell_rules': len(rules['sell_rules']),
            'indicators': [(item['kind'], item['period']) for item in rules['indicators']],
        }, flush=True)
    finally:
        await pool.close()
        await api.close_provider_client()


if __name__ == '__main__':
    if len(sys.argv) != 2 or not youtube_strategy.VIDEO_ID.fullmatch(sys.argv[1]):
        raise SystemExit('Provide exactly one public YouTube video ID')
    logging.basicConfig(level=logging.INFO)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    try:
        asyncio.run(main(sys.argv[1]))
    except Exception as exc:
        print('YOUTUBE_IMPORT_CHECK', {'ok': False, 'type': type(exc).__name__,
              'code': str(exc) if isinstance(exc, api.AnalysisError) else 'failed'}, flush=True)
        raise SystemExit(1)
