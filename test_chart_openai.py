"""OpenAI wire contract and safe provider selection (no live paid calls)."""
import copy
import json
import os
import unittest
from unittest.mock import AsyncMock, patch
import httpx
import chart_analysis as api
import chart_openai
from test_chart_signals import IMAGE, VALID


def response():
    return {'status': 'completed', 'output': [
        {'type': 'reasoning', 'summary': []},
        {'type': 'message', 'role': 'assistant', 'status': 'completed',
         'content': [{'type': 'output_text', 'text': json.dumps(VALID)}]}]}


class OpenAITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, CHART_PROVIDER='openai', OPENAI_API_KEY='test-secret', GEMINI_API_KEY='other-secret')
        self.env.start()
        self.addCleanup(self.env.stop)

    async def test_image_schema_auth_and_strategy(self):
        def handler(req):
            self.assertEqual(str(req.url), 'https://api.openai.com/v1/responses')
            self.assertEqual(req.headers['authorization'], 'Bearer test-secret')
            self.assertNotIn('x-goog-api-key', req.headers)
            body = json.loads(req.content)
            self.assertEqual(body['model'], 'gpt-6-luna')
            self.assertFalse(body['store'])
            self.assertEqual(body['reasoning']['effort'], 'low')
            self.assertIn(api.INSTRUCTIONS, body['instructions'])
            self.assertIn('Hindi', body['instructions'])
            self.assertEqual(body['text']['format']['schema'], api.RESULT_SCHEMA)
            self.assertTrue(body['text']['format']['strict'])
            self.assertTrue(body['input'][0]['content'][1]['image_url'].startswith('data:image/png;base64,'))
            self.assertNotIn('test-secret', req.content.decode())
            return httpx.Response(200, json=response())
        self.assertEqual((await api.analyse(IMAGE, 'hi', transport=httpx.MockTransport(handler)))['direction'], 'BUY')

    async def test_missing_key_does_not_use_other_provider(self):
        with patch.dict(os.environ, OPENAI_API_KEY=''):
            self.assertFalse(api.configured())
            with self.assertRaises(api.AnalysisError) as error:
                await api.analyse(IMAGE, 'en')
            self.assertEqual(str(error.exception), 'configuration')

    async def test_quota_error_is_not_retried_or_fallen_back(self):
        seen = []
        def handler(req):
            seen.append(req)
            return httpx.Response(429, json={'error': {'code': 'insufficient_quota'}})
        with self.assertRaises(api.AnalysisError):
            await api.analyse(IMAGE, 'en', transport=httpx.MockTransport(handler))
        self.assertEqual(len(seen), 1)

    async def test_transient_retry_reserves_budget(self):
        seen = []
        reserve = AsyncMock()
        def handler(req):
            seen.append(req)
            return httpx.Response(503 if len(seen) == 1 else 200, json=response())
        with patch.object(api, 'retry_delay', return_value=0):
            result = await api.analyse(IMAGE, 'en', transport=httpx.MockTransport(handler), before_retry=reserve)
        self.assertEqual(result['direction'], 'BUY')
        reserve.assert_awaited_once()
        self.assertEqual(len(seen), 2)

    def test_incomplete_refusal_tools_and_bad_signal_rejected(self):
        cases = []
        p=response();p['status']='incomplete';cases.append(p)
        p=response();p['output'][1]['content']=[{'type':'refusal','refusal':'no'}];cases.append(p)
        p=response();p['output'].append(copy.deepcopy(p['output'][1]));cases.append(p)
        p=response();p['output'].append({'type':'function_call'});cases.append(p)
        p=response();p['output'][1]['content'][0]['text']='{}';cases.append(p)
        p=response();p['output'][1]['content'][0]['text']=json.dumps(dict(VALID,direction='SELL'));cases.append(p)
        p=response();p['output'][1]['content'][0]['text']='{"direction":"BUY","direction":"SELL"}';cases.append(p)
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(api.AnalysisError):
                chart_openai.parse_response(payload)


if __name__ == '__main__':
    unittest.main()
