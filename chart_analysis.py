"""Screenshot analysis with explicit provider selection; no cross-provider fallback."""
import asyncio
import base64
import io
import json
import logging
import math
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
import chart_strategy

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MODEL = 'gemini-3.8-flash'
API_URL = 'https://generativelanguage.googleapis.com/v1beta/models/' + MODEL + ':generateContent'
DAILY_LIMIT = max(1, int(os.getenv('CHART_DAILY_LIMIT', '5')))
GLOBAL_LIMIT = max(1, int(os.getenv('CHART_GLOBAL_DAILY_LIMIT', '200')))
MAX_CONCURRENT = 4
# Allow room for thinking and final JSON; still reject truncated responses.
MAX_OUTPUT_TOKENS = 8192
HTTP_TIMEOUT_SECONDS = 80
TOTAL_TIMEOUT_SECONDS = 90  # Below the existing three-minute trial reservation.
MAX_REQUEST_ATTEMPTS = 3
RETRYABLE_HTTP_STATUS = frozenset((500, 502, 503, 504))
_clients = {}


@asynccontextmanager
async def provider_client(transport=None):
    """Reuse connections between uploads; mock transports stay isolated."""
    if transport is not None:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, transport=transport) as client:
            yield client
        return
    loop = asyncio.get_running_loop()
    client = _clients.get(loop)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS,
            limits=httpx.Limits(max_connections=MAX_CONCURRENT,
                               max_keepalive_connections=MAX_CONCURRENT,
                               keepalive_expiry=120))
        _clients[loop] = client
    yield client


async def close_provider_client():
    client = _clients.pop(asyncio.get_running_loop(), None)
    if client is not None:
        await client.aclose()

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
    'http_status', 'transport', 'timeout',
    'strategy_quote', 'strategy_unusable', 'strategy_fields',
))


class AnalysisError(Exception):
    """Only a safe error code, never response bodies or credential-bearing URLs."""

    def __init__(self, code, detail='unspecified'):
        super().__init__(code)
        self.detail = detail if isinstance(detail, str) and detail in REJECTION_REASONS else 'unspecified'


def provider():
    return os.getenv('CHART_PROVIDER', 'gemini').strip().lower()


def model():
    return 'gpt-6-luna' if provider() == 'openai' else MODEL


def configured():
    name = {'gemini': 'GEMINI_API_KEY', 'openai': 'OPENAI_API_KEY'}.get(provider())
    return bool(name and os.getenv(name, '').strip())


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
            'without code fences, indentation or introductory text. '
            'For concise output, use at most 12 words each for trend and momentum, '
            'at most 35 words for reason, and at most 18 words for invalidation. '
            'Return exactly two distinct evidence observations of at most 18 words each. '
            'Retain the visible basis and any missing confirmation or opposing evidence. '
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


def parse_response(payload, *, validator=None):
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
        return (validator or validate_result)(result)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise AnalysisError('response', detail) from exc


def validate_result(result):
    detail = 'fields'
    try:
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


def retry_delay(attempt, response=None):
    """Bounded exponential backoff with jitter; respect a longer Retry-After."""
    delay = 2 ** attempt + secrets.randbelow(1000) / 1000
    if response is not None:
        value = response.headers.get('retry-after', '')
        try:
            requested = float(value)
        except ValueError:
            try:
                requested = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                requested = 0
        if math.isfinite(requested):
            delay = max(delay, requested)
    return delay


async def request_response(data, language, context='', *, transport=None, before_retry=None, body=None):
    """Retry transient failures within one deadline and the durable request budget."""
    selected = provider()
    key_name = {'gemini': 'GEMINI_API_KEY', 'openai': 'OPENAI_API_KEY'}.get(selected)
    key = os.getenv(key_name, '').strip() if key_name else ''
    if not key:
        raise AnalysisError('configuration')
    if body is None:
        body = request_body(data, language, context)
    url = API_URL
    headers = {'x-goog-api-key': key}
    if selected == 'openai':
        import chart_openai
        body = chart_openai.request_body(body, model())
        url = 'https://api.openai.com/v1/responses'
        headers = {'Authorization': 'Bearer ' + key}
    started = time.monotonic()
    attempt = 0
    outcome = 'failed'
    try:
        async with asyncio.timeout(TOTAL_TIMEOUT_SECONDS) as deadline:
            async with provider_client(transport) as client:
                for attempt in range(1, MAX_REQUEST_ATTEMPTS + 1):
                    response = None
                    attempt_started = time.monotonic()
                    try:
                        response = await client.post(url, headers=headers, json=body)
                    except httpx.TransportError as exc:
                        detail = 'timeout' if isinstance(exc, httpx.TimeoutException) else 'transport'
                        failure = AnalysisError('service', detail)
                        logging.warning('Chart provider failed: provider=%s attempt=%s detail=%s', selected, attempt, detail)
                    else:
                        status = response.status_code
                        if status == 200:
                            payload = response.json()
                            outcome = 'received'
                            counts = usage_counts(payload, selected)
                            logging.info('Chart provider timing: attempt=%s elapsed_ms=%s output_tokens=%s thinking_tokens=%s',
                                         attempt, round((time.monotonic() - attempt_started) * 1000), *counts)
                            if attempt > 1:
                                logging.info('Chart provider recovered: provider=%s attempt=%s', selected, attempt)
                            return payload
                        code = ('configuration' if status in (400, 401, 402, 403, 404) else
                                'busy' if status == 429 else 'service')
                        failure = AnalysisError(code, 'http_status')
                        # Log only status/attempt, never response bodies or headers.
                        logging.warning('Chart provider failed: provider=%s attempt=%s status=%s', selected, attempt, status)
                        if status not in RETRYABLE_HTTP_STATUS:
                            raise failure
                    if attempt == MAX_REQUEST_ATTEMPTS:
                        raise failure
                    delay = retry_delay(attempt, response)
                    if delay >= deadline.when() - asyncio.get_running_loop().time():
                        raise failure
                    await asyncio.sleep(delay)
                    if before_retry is not None:
                        await before_retry()
    except TimeoutError as exc:
        raise AnalysisError('service', 'timeout') from exc
    except httpx.HTTPError as exc:
        raise AnalysisError('service', 'transport') from exc
    except ValueError as exc:
        raise AnalysisError('response', 'json') from exc
    finally:
        logging.info('Chart provider total: outcome=%s attempts=%s elapsed_ms=%s',
                     outcome, attempt, round((time.monotonic() - started) * 1000))


def usage_counts(payload, selected):
    usage = payload.get('usage' if selected == 'openai' else 'usageMetadata', {}) if isinstance(payload, dict) else {}
    if not isinstance(usage, dict):
        return [None, None]
    if selected == 'openai':
        details = usage.get('output_tokens_details') or {}
        values = [usage.get('output_tokens'), details.get('reasoning_tokens') if isinstance(details, dict) else None]
    else:
        values = [usage.get('candidatesTokenCount'), usage.get('thoughtsTokenCount')]
    return [value if type(value) is int else None for value in values]


async def analyse(data, language, context='', *, transport=None, before_retry=None, strategy=None):
    if strategy is not None:
        import youtube_strategy
        return await youtube_strategy.analyse_chart(data, language, context, strategy,
                                                   transport=transport, before_retry=before_retry)
    payload = await request_response(data, language, context, transport=transport, before_retry=before_retry)
    try:
        if provider() == 'openai':
            import chart_openai
            return chart_openai.parse_response(payload)
        return parse_response(payload)
    except AnalysisError as exc:
        # Counts and local reason codes only, never model text/images/credentials.
        logging.warning('Chart response rejected: provider=%s code=%s detail=%s output_tokens=%s thinking_tokens=%s',
                        provider(), str(exc), exc.detail, *usage_counts(payload, provider()))
        raise


async def structured_request(body, validator, *, transport=None, before_retry=None):
    """Use the configured provider and the same timeout, retries and error handling."""
    payload = await request_response(None, 'en', body=body, transport=transport, before_retry=before_retry)
    if provider() == 'openai':
        import chart_openai
        return chart_openai.parse_response(payload, validator=validator)
    return parse_response(payload, validator=validator)


# Durable request budgets survive deploys. Row 0 is the global daily budget;
# all reservations take one transaction lock before checking either counter.
SCHEMA = """
CREATE TABLE IF NOT EXISTS chart_analysis_usage (
 tg_id BIGINT PRIMARY KEY, usage_day DATE NOT NULL,
 requests INTEGER NOT NULL DEFAULT 0, last_request TIMESTAMPTZ NOT NULL
);
"""


async def reserve(pool, tg_id, *, retry=False):
    if tg_id <= 0:
        return 'private'
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute('SELECT pg_advisory_xact_lock(746230901)')
            now = await conn.fetchval('SELECT clock_timestamp()')
            day = now.astimezone(timezone.utc).date()
            rows = await conn.fetch('SELECT * FROM chart_analysis_usage WHERE tg_id IN (0, $1)', tg_id)
            verified = bool(await conn.fetchval('SELECT verified FROM users WHERE tg_id=$1', tg_id))
            blocked = reservation_refusal(rows, tg_id, now, verified=verified, retry=retry)
            if blocked:
                return blocked
            for key in (0, tg_id):
                await conn.execute('''INSERT INTO chart_analysis_usage (tg_id, usage_day, requests, last_request)
                    VALUES ($1, $2, 1, $3) ON CONFLICT (tg_id) DO UPDATE SET
                    requests = CASE WHEN chart_analysis_usage.usage_day = $2
                                    THEN chart_analysis_usage.requests + 1 ELSE 1 END,
                    usage_day = $2, last_request = $3''', key, day, now)
    return None


def reservation_refusal(rows, tg_id, now, *, verified=False, retry=False):
    day = now.astimezone(timezone.utc).date()
    usage = {row['tg_id']: row for row in rows}
    for key, limit in ((tg_id, DAILY_LIMIT), (0, GLOBAL_LIMIT)):
        # Verified users are capped by delivered signals, not the trial's
        # five API attempts. The global provider cap still applies.
        if key == tg_id and verified:
            continue
        row = usage.get(key)
        if row and row['usage_day'] == day and row['requests'] >= limit:
            return 'limit'
    # Active uploads are guarded by the handler and durable trial slots.
    # A completed or failed request must not impose an extra waiting period.
    return None
