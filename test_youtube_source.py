"""Cloud caption failure recovery without real keys, videos or database calls."""
import json
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import chart_analysis as api
import youtube_source as source
import youtube_strategy as yt

VIDEO = 'JXGAW2LXAaA'
TEXT = ('The lesson uses EMA 50 and RSI 14 on completed one-minute candles. '
        'Buy above the EMA with RSI above 50. Sell below with RSI below 50. '
        'Avoid sideways price around the EMA and RSI near its midpoint.')


def response(notes=TEXT, *, available=True, status='completed'):
    return {'status': status, 'steps': [
        {'type': 'thought', 'summary': [{'type': 'text', 'text': 'not source text'}]},
        {'type': 'model_output', 'content': [{'type': 'text', 'text': json.dumps(
            {'available': available, 'notes': notes})}]}]}


class SourceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict(os.environ, GEMINI_API_KEY='test-secret', YOUTUBE_VIDEO_FALLBACK='true')
        self.env.start()
        self.addCleanup(self.env.stop)

    async def test_cloud_block_uses_video_reader_then_preserves_provenance(self):
        seen = []
        def handler(req):
            seen.append(req)
            return httpx.Response(200, json=response())
        reserve = AsyncMock()
        with patch.object(yt, 'fetch_transcript', AsyncMock(side_effect=api.AnalysisError('transcript'))), \
             patch.object(source, 'save_source', AsyncMock()) as save:
            text, origin = await source.load(VIDEO, 'en', before_request=reserve,
                                             transport=httpx.MockTransport(handler))
        self.assertEqual((text, origin), (TEXT, 'video_analysis'))
        reserve.assert_awaited_once()
        save.assert_awaited_once_with(None, VIDEO, 'en', TEXT, 'video_analysis')
        self.assertEqual(len(seen), 1)
        body = json.loads(seen[0].content)
        self.assertEqual(body['input'][0]['uri'], 'https://www.youtube.com/watch?v=' + VIDEO)
        self.assertFalse(body['store'])
        self.assertNotIn('test-secret', str(seen[0].url))
        self.assertEqual(seen[0].headers['x-goog-api-key'], 'test-secret')

    async def test_cache_and_successful_captions_do_not_call_video_reader(self):
        with patch.object(source, 'cached', AsyncMock(return_value=(TEXT, 'captions'))), \
             patch.object(yt, 'fetch_transcript', AsyncMock()) as direct, \
             patch.object(source, 'read_video', AsyncMock()) as video:
            self.assertEqual(await source.load(VIDEO, 'en'), (TEXT, 'captions'))
            direct.assert_not_awaited();video.assert_not_awaited()
        with patch.object(yt, 'fetch_transcript', AsyncMock(return_value=TEXT)), \
             patch.object(source, 'read_video', AsyncMock()) as video:
            self.assertEqual(await source.load(VIDEO, 'en'), (TEXT, 'captions'))
            video.assert_not_awaited()

    async def test_missing_key_disabled_reader_and_budget_never_make_an_extra_call(self):
        with patch.object(yt, 'fetch_transcript', AsyncMock(side_effect=api.AnalysisError('transcript'))), \
             patch.object(source, 'read_video', AsyncMock()) as video:
            for settings in ({'GEMINI_API_KEY': ''}, {'YOUTUBE_VIDEO_FALLBACK': 'false'}):
                with patch.dict(os.environ, settings), self.assertRaisesRegex(api.AnalysisError, '^transcript$'):
                    await source.load(VIDEO, 'en')
            with self.assertRaisesRegex(api.AnalysisError, '^cancelled$'):
                await source.load(VIDEO, 'en', before_request=AsyncMock(side_effect=api.AnalysisError('cancelled')))
            video.assert_not_awaited()

    async def test_private_or_unavailable_video_and_truncation_are_not_accepted(self):
        for payload in (response(available=False), response(status='incomplete'), response(notes='too short'),
                        {'status': 'completed', 'steps': []}, {'status': 'failed'}, None):
            with self.subTest(payload=payload), self.assertRaisesRegex(api.AnalysisError, '^transcript$'):
                source.parse_video(payload)
        with self.assertRaisesRegex(api.AnalysisError, '^transcript_size$'):
            source.parse_video(response(notes='x' * (yt.MAX_TRANSCRIPT_CHARS + 1)))

    async def test_api_errors_are_sanitized_and_not_retried_without_budget(self):
        for status in (400, 403, 429, 500):
            seen = []
            def handler(req):
                seen.append(req)
                return httpx.Response(status, text='PRIVATE_RESPONSE')
            with self.assertRaisesRegex(api.AnalysisError, '^transcript$'):
                await source.read_video(VIDEO, 'en', transport=httpx.MockTransport(handler))
            self.assertEqual(len(seen), 1)
        with self.assertRaisesRegex(api.AnalysisError, '^video_url$'):
            await source.load('https://127.0.0.1', 'en')

    async def test_cache_never_shares_pasted_transcripts_and_enforces_retention(self):
        conn = MagicMock()
        conn.execute = AsyncMock()
        conn.transaction.return_value.__aenter__ = AsyncMock()
        conn.transaction.return_value.__aexit__ = AsyncMock()
        pool = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock()
        with self.assertRaisesRegex(api.AnalysisError, '^transcript$'):
            await source.save_source(pool, VIDEO, 'en', TEXT, 'pasted')
        conn.execute.assert_not_awaited()
        await source.save_source(pool, VIDEO, 'en', TEXT, 'captions')
        sql = ' '.join(call.args[0] for call in conn.execute.await_args_list)
        self.assertIn("interval '7 days'", sql)
        self.assertIn('OFFSET 1000', sql)
        self.assertEqual(conn.execute.await_args_list[2].args[1:], (VIDEO, 'en', TEXT, 'captions'))


if __name__ == '__main__':
    unittest.main()
