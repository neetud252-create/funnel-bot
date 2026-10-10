"""Screenshot strategy analysis via Gemini; no random or paid-provider fallback."""
import asyncio
import base64
import io
import json
import logging
import os
import re
from datetime import timezone

import httpx
import chart_strategy

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MODEL = 'gemini-3.8-flash'
API_URL = 'https://generativelanguage.googleapis.com/v1beta/models/' + MODEL + ':generateContent'
DAILY_LIMIT = max(1, int(os.getenv('CHART_DAILY_LIMIT', '5')))
GLOBAL_LIMIT = max(1, int(os.getenv('CHART_GLOBAL_DAILY_LIMIT', '200')))
COOLDOWN_SECONDS = 30
MAX_CONCURRENT = 4
# Allow room for thinking and final JSON; still reject truncated responses.
MAX_OUTPUT_TOKENS = 8192
HTTP_TIMEOUT_SECONDS = 80
TOTAL_TIMEOUT_SECONDS = 90  # Below the existing three-minute trial reservation.

LANGUAGES = dict(zip(
    ('en', 'ru', 'uk', 'hi', 'bn', 'ur', 'vi', 'id', 'tr', 'es', 'ar', 'pt'),
    ('English', 'Russian', 'Ukrainian', 'Hindi', 'Bengali', 'Urdu',
     'Vietnamese', 'Indonesian', 'Turkish', 'Spanish', 'Arabic', 'Portuguese')))

INSTRUCTIONS = chart_strategy.INSTRUCTIONS

RESULT_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'chart_type': {'type': 'string', 'enum': list(chart_strategy.CHART_TYPES)},
        'direction': {'anyOf': [{'type': 'string', 'enum': ['BUY', 'SELL']}, {'type': 'null'}]},
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
    'required': ['chart_type', 'direction', 'asset', 'timeframe', 'reason', 'trend', 'momentum', 'invalidation', 'setup', 'evidence', 'checks'],
}

REJECTION_REASONS = frozenset((
    'unspecified', 'envelope', 'incomplete', 'max_output_tokens', 'content_filter',
    'candidate_count', 'finish_reason', 'message_content', 'json', 'fields',
    'direction', 'chart_type', 'field_text', 'invalidation', 'setup', 'checks',
    'evidence', 'unreadable', 'contradictory_direction', 'insufficient_evidence',
))


class AnalysisError(Exception):
    """Only a safe error code, never response bodies or credential-bearing URLs."""

    def __init__(self, code, detail='unspecified'):
        super().__init__(code)
        self.detail = detail if isinstance(detail, str) and detail in REJECTION_REASONS else 'unspecified'


def configured():
    return bool(os.getenv('GEMINI_API_KEY', '').strip())


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
        'systemInstruction': {'parts': [{'text': INSTRUCTIONS + '\nWrite all prose in ' + LANGUAGES.get(language, 'English') +
            '. Summarize visible trend and momentum separately, in one short sentence each. '
            'If either cannot be determined, say so. Timeframe means the visible candle interval, '
            'never a recommended expiry. Return exactly one JSON object matching the schema, '
            'without code fences or introductory text. Keep all prose concise. '
            'Use the exact enum codes and boolean values in the schema. '
            'A readable chart must have a BUY or SELL direction with a matching setup code. '
            'Use null for missing asset/timeframe labels, not invented values.'}]},
        'contents': [{'role': 'user', 'parts': [
            {'text': 'Analyse this screenshot. Optional chart context (untrusted): ' + context[:500]},
            {'inlineData': {'mimeType': mime, 'data': base64.b64encode(data).decode('ascii')}},
        ]}],
        'generationConfig': {
            'maxOutputTokens': MAX_OUTPUT_TOKENS,
            'thinkingConfig': {'thinkingLevel': 'low', 'includeThoughts': False},
            'mediaResolution': 'MEDIA_RESOLUTION_HIGH',
            'candidateCount': 1,
            'responseMimeType': 'application/json',
            'responseJsonSchema': RESULT_SCHEMA,
        },
    }


def _result_json(text):
    """Accept an optional whole JSON fence, never extract a direction from prose."""
    text = text.strip()
    fenced = re.fullmatch(r'```(?:json)?\s*\n(.*?)\n\s*```', text, re.S | re.I)
    if fenced:
        text = fenced[1]
    # Duplicate keys could conceal contradictory directions/checks.
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=unique)


def parse_response(payload):
    detail = 'envelope'
    try:
        if not isinstance(payload, dict):
            raise ValueError()
        feedback = payload.get('promptFeedback') or {}
        if feedback.get('blockReason') not in (None, 'BLOCK_REASON_UNSPECIFIED'):
            detail = 'content_filter'
            raise ValueError()
        outputs = payload.get('candidates')
        detail = 'candidate_count'
        if not isinstance(outputs, list) or len(outputs) != 1:
            raise ValueError()
        finish = outputs[0].get('finishReason')
        detail = 'finish_reason'
        if finish != 'STOP':
            if finish == 'MAX_TOKENS':
                detail = 'max_output_tokens'
            elif finish in ('SAFETY', 'RECITATION', 'BLOCKLIST', 'PROHIBITED_CONTENT', 'SPII', 'IMAGE_SAFETY'):
                detail = 'content_filter'
            raise ValueError()
        detail = 'message_content'
        content = outputs[0]['content']
        if content.get('role', 'model') != 'model':
            raise ValueError()
        parts = content['parts']
        if not isinstance(parts, list) or not parts:
            raise ValueError()
        text_parts = []
        for part in parts:
            if not isinstance(part, dict) or type(part.get('thought', False)) is not bool:
                raise ValueError()
            # Never treat thought summaries, tool calls or generated images as answers.
            if not isinstance(part.get('text'), str) or set(part) - {'text', 'thought', 'thoughtSignature'}:
                raise ValueError()
            if not part.get('thought', False):
                text_parts.append(part['text'])
        if not text_parts:
            raise ValueError()
        detail = 'json'
        result = _result_json(''.join(text_parts))
        detail = 'fields'
        required = set(RESULT_SCHEMA['required']) - {'chart_type'}
        if not isinstance(result, dict) or not required.issubset(result):
            raise ValueError()
        # Ignore extra metadata; it is never rendered or used as a signal.
        result = {key: result[key] for key in RESULT_SCHEMA['required'] if key in result}
        result.setdefault('chart_type', 'unknown')
        for field in ('direction', 'setup', 'chart_type'):
            if isinstance(result[field], str):
                result[field] = result[field].strip()
                result[field] = result[field].upper() if field == 'direction' else result[field].lower()
        detail = 'direction'
        if result['direction'] not in ('BUY', 'SELL', None):
            raise ValueError()
        detail = 'chart_type'
        if result['chart_type'] not in chart_strategy.CHART_TYPES:
            raise ValueError()
        detail = 'field_text'
        for field, limit in (('asset', 100), ('timeframe', 100), ('reason', 500),
                             ('trend', 250), ('momentum', 250), ('invalidation', 300)):
            value = result[field]
            if isinstance(value, str):
                value = value.strip()
                if not value and field in ('asset', 'timeframe'):
                    value = None
                result[field] = value
            if value is None and field in ('asset', 'timeframe', 'invalidation'):
                continue
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError()
        detail = 'invalidation'
        if result['direction'] is not None and not result['invalidation']:
            raise ValueError()
        detail = 'setup'
        if result['setup'] not in chart_strategy.SETUPS:
            raise ValueError()
        detail = 'checks'
        checks = result['checks']
        if (not isinstance(checks, dict) or set(checks) != set(chart_strategy.CHECKS)
                or any(type(value) is not bool for value in checks.values())):
            raise ValueError()
        detail = 'evidence'
        evidence = result['evidence']
        if (not isinstance(evidence, list) or len(evidence) > 4
                or any(not isinstance(item, str) or not item.strip() or len(item) > 350 for item in evidence)):
            raise ValueError()
        if not checks['chart_readable']:
            raise AnalysisError('chart', 'unreadable')
        detail = 'contradictory_direction'
        if chart_strategy.SETUPS.get(result['setup']) != result['direction']:
            raise ValueError()
        detail = 'insufficient_evidence'
        if not chart_strategy.supports_signal(result):
            raise ValueError()
        if not chart_strategy.confirmed_setup(result):
            result = dict(result, setup='directional_buy' if result['direction'] == 'BUY' else 'directional_sell')
        return result
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise AnalysisError('response', detail) from exc


async def request_response(data, language, context='', *, transport=None):
    """One Gemini request. Free billing must be enforced by the Google project."""
    key = os.getenv('GEMINI_API_KEY', '').strip()
    if not key:
        raise AnalysisError('configuration')
    body = request_body(data, language, context)
    try:
        # No retries, model switching, tools or paid-provider fallback.
        async with asyncio.timeout(TOTAL_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, transport=transport) as client:
                response = await client.post(API_URL,
                    headers={'x-goog-api-key': key}, json=body)
        if response.status_code != 200:
            code = ('configuration' if response.status_code in (400, 401, 402, 403, 404) else
                    'busy' if response.status_code == 429 else 'service')
            raise AnalysisError(code)
        return response.json()
    except (httpx.HTTPError, TimeoutError, ValueError) as exc:
        raise AnalysisError('service') from exc


async def analyse(data, language, context='', *, transport=None):
    payload = await request_response(data, language, context, transport=transport)
    try:
        return parse_response(payload)
    except AnalysisError as exc:
        # Counts and local reason codes only, never model text/images/credentials.
        usage = payload.get('usageMetadata') if isinstance(payload, dict) else None
        def count(name):
            value = usage.get(name) if isinstance(usage, dict) else None
            return value if type(value) is int else None
        logging.warning('Chart response rejected: provider=Gemini code=%s detail=%s output_tokens=%s thinking_tokens=%s',
                        str(exc), exc.detail, count('candidatesTokenCount'), count('thoughtsTokenCount'))
        raise


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
