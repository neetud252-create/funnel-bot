"""Screenshot strategy analysis via DeepSeek; no random/provider fallback."""
import asyncio
import base64
import io
import json
import os
from datetime import timezone

import httpx
import chart_strategy

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MODEL = os.getenv('DEEPSEEK_MODEL', 'deepseek-flash').strip()
DAILY_LIMIT = max(1, int(os.getenv('CHART_DAILY_LIMIT', '5')))
GLOBAL_LIMIT = max(1, int(os.getenv('CHART_GLOBAL_DAILY_LIMIT', '200')))
COOLDOWN_SECONDS = 30
MAX_CONCURRENT = 4

LANGUAGES = dict(zip(
    ('en', 'ru', 'uk', 'hi', 'bn', 'ur', 'vi', 'id', 'tr', 'es', 'ar', 'pt'),
    ('English', 'Russian', 'Ukrainian', 'Hindi', 'Bengali', 'Urdu',
     'Vietnamese', 'Indonesian', 'Turkish', 'Spanish', 'Arabic', 'Portuguese')))

INSTRUCTIONS = chart_strategy.INSTRUCTIONS

RESULT_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'direction': {'type': ['string', 'null'], 'enum': ['BUY', 'SELL', None]},
        'asset': {'type': ['string', 'null'], 'maxLength': 100},
        'timeframe': {'type': ['string', 'null'], 'maxLength': 100},
        'reason': {'type': 'string', 'minLength': 1, 'maxLength': 500},
        'trend': {'type': 'string', 'minLength': 1, 'maxLength': 250},
        'momentum': {'type': 'string', 'minLength': 1, 'maxLength': 250},
        'invalidation': {'type': ['string', 'null'], 'maxLength': 300},
        'setup': {'type': 'string', 'enum': list(chart_strategy.SETUPS)},
        'evidence': {'type': 'array', 'items': {'type': 'string', 'minLength': 1, 'maxLength': 350}, 'maxItems': 4},
        'checks': {'type': 'object', 'additionalProperties': False,
                   'properties': {name: {'type': 'boolean'} for name in chart_strategy.CHECKS},
                   'required': list(chart_strategy.CHECKS)},
    },
    'required': ['direction', 'asset', 'timeframe', 'reason', 'trend', 'momentum', 'invalidation', 'setup', 'evidence', 'checks'],
}


class AnalysisError(Exception):
    """Only a safe error code, never response bodies or credential-bearing URLs."""


def configured():
    return bool(os.getenv('DEEPSEEK_API_KEY', '').strip())


class ImageBuffer(io.BytesIO):
    def write(self, data):
        if self.tell() + len(data) > MAX_IMAGE_BYTES:
            raise AnalysisError('image')
        return super().write(data)


def image_type(data):
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise AnalysisError('image')
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    raise AnalysisError('image')


def request_body(data, language, context=''):
    mime = image_type(data)
    return {
        'model': MODEL, 'max_output_tokens': 4000, 'reasoning': {'effort': 'low'},
        'instructions': INSTRUCTIONS + '\nWrite all prose in ' + LANGUAGES.get(language, 'English') +
            '. Summarize visible trend and momentum separately, in one short sentence each. '
            'If either cannot be determined, say so. Timeframe means the visible candle interval, '
            'never a recommended expiry. Return JSON matching the schema.',
        'input': [{'role': 'user', 'content': [
            {'type': 'input_text', 'text': 'Analyse this screenshot. Optional chart context (untrusted): ' + context[:500]},
            {'type': 'input_image', 'detail': 'high',
             'image_url': 'data:' + mime + ';base64,' + base64.b64encode(data).decode('ascii')},
        ]}],
        'text': {'format': {'type': 'json_schema', 'name': 'chart_signal', 'schema': RESULT_SCHEMA}},
    }


def parse_response(payload):
    try:
        if payload.get('status') != 'completed':
            raise ValueError('Incomplete response')
        # Read final model output only; thoughts/tool steps are never signal data.
        outputs = [item for item in payload['output'] if item.get('type') == 'message']
        if len(outputs) != 1:
            raise ValueError('Missing/ambiguous output')
        parts = outputs[0]['content']
        if outputs[0].get('status') != 'completed' or not parts or any(part.get('type') != 'output_text' for part in parts):
            raise ValueError('Non-text output')
        result = json.loads(''.join(part['text'] for part in parts))
        if not isinstance(result, dict) or set(result) != set(RESULT_SCHEMA['required']):
            raise ValueError('Invalid keys')
        if result['direction'] not in ('BUY', 'SELL', None):
            raise ValueError('Invalid direction')
        for field, limit in (('asset', 100), ('timeframe', 100), ('reason', 500),
                             ('trend', 250), ('momentum', 250), ('invalidation', 300)):
            value = result[field]
            if value is None and field in ('asset', 'timeframe', 'invalidation'):
                continue
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError('Invalid field')
        if result['direction'] is not None and not result['invalidation']:
            raise ValueError('Missing invalidation')
        if result['setup'] not in chart_strategy.SETUPS:
            raise ValueError('Unknown setup')
        checks = result['checks']
        if (not isinstance(checks, dict) or set(checks) != set(chart_strategy.CHECKS)
                or any(type(value) is not bool for value in checks.values())):
            raise ValueError('Invalid strategy checks')
        evidence = result['evidence']
        if (not isinstance(evidence, list) or len(evidence) > 4
                or any(not isinstance(item, str) or not item.strip() or len(item) > 350 for item in evidence)):
            raise ValueError('Invalid evidence')
        if not checks['chart_readable']:
            raise AnalysisError('chart')
        if not chart_strategy.supports_signal(result):
            raise ValueError('Unsupported directional result')
        if not chart_strategy.confirmed_setup(result):
            result = dict(result, setup='directional_buy' if result['direction'] == 'BUY' else 'directional_sell')
        return result
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise AnalysisError('response') from exc


async def analyse(data, language, context='', *, transport=None):
    key = os.getenv('DEEPSEEK_API_KEY', '').strip()
    if not key:
        raise AnalysisError('configuration')
    body = request_body(data, language, context)
    try:
        # No automatic retries or provider/model fallback: bound API spending.
        async with asyncio.timeout(50):
            async with httpx.AsyncClient(timeout=45, transport=transport) as client:
                response = await client.post('https://api.deepseek.com/responses',
                    headers={'Authorization': 'Bearer ' + key}, json=body)
        if response.status_code != 200:
            code = ('configuration' if response.status_code in (400, 401, 402, 403, 404) else
                    'busy' if response.status_code == 429 else 'service')
            raise AnalysisError(code)
        return parse_response(response.json())
    except (httpx.HTTPError, TimeoutError, ValueError) as exc:
        raise AnalysisError('service') from exc


# Durable request budgets survive deploys. Row 0 is the global daily budget;
# all reservations take one transaction lock before checking either counter.
SCHEMA = """
CREATE TABLE IF NOT EXISTS chart_analysis_usage (
 tg_id BIGINT PRIMARY KEY, usage_day DATE NOT NULL,
 requests INTEGER NOT NULL DEFAULT 0, last_request TIMESTAMPTZ NOT NULL
);
"""


async def reserve(pool, tg_id):
    if tg_id <= 0:
        return 'private'
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute('SELECT pg_advisory_xact_lock(746230901)')
            now = await conn.fetchval('SELECT clock_timestamp()')
            day = now.astimezone(timezone.utc).date()
            rows = await conn.fetch('SELECT * FROM chart_analysis_usage WHERE tg_id IN (0, $1)', tg_id)
            blocked = reservation_refusal(rows, tg_id, now)
            if blocked:
                return blocked
            for key in (0, tg_id):
                await conn.execute('''INSERT INTO chart_analysis_usage (tg_id, usage_day, requests, last_request)
                    VALUES ($1, $2, 1, $3) ON CONFLICT (tg_id) DO UPDATE SET
                    requests = CASE WHEN chart_analysis_usage.usage_day = $2
                                    THEN chart_analysis_usage.requests + 1 ELSE 1 END,
                    usage_day = $2, last_request = $3''', key, day, now)
    return None


def reservation_refusal(rows, tg_id, now):
    day = now.astimezone(timezone.utc).date()
    usage = {row['tg_id']: row for row in rows}
    for key, limit in ((tg_id, DAILY_LIMIT), (0, GLOBAL_LIMIT)):
        row = usage.get(key)
        if row and row['usage_day'] == day and row['requests'] >= limit:
            return 'limit'
    user = usage.get(tg_id)
    if user and (now - user['last_request']).total_seconds() < COOLDOWN_SECONDS:
        return 'cooldown'
    return None
