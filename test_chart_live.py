"""Opt-in Gemini diagnostic: one request, no bot/database changes.

Run: python test_chart_live.py path/to/chart.png
Requires GEMINI_API_KEY from a billing-disabled project for free-only use.
Use an approved chart-only image; never send private account or chat details.
"""
import asyncio
import sys
import time
from pathlib import Path

import chart_analysis as api


def known(value, values):
    return value if isinstance(value, str) and value in values else 'other'


async def main(path):
    if not api.configured():
        raise SystemExit('GEMINI_API_KEY is not configured')
    if Path(path).stat().st_size > api.MAX_IMAGE_BYTES:
        raise SystemExit('Image exceeds the 8 MiB limit')
    data = Path(path).read_bytes()
    started = time.monotonic()
    try:
        # Same transport, credentials, payload and deadlines as the Telegram flow.
        payload = await api.request_response(data, 'en')
        print('elapsed_seconds:', round(time.monotonic() - started, 2))
        print('max_output_tokens:', api.MAX_OUTPUT_TOKENS)
        usage = payload.get('usageMetadata') if isinstance(payload, dict) else None
        if isinstance(usage, dict):
            print('usage:', {k: v for k, v in usage.items() if k in (
                'promptTokenCount', 'candidatesTokenCount', 'thoughtsTokenCount',
                'totalTokenCount') and type(v) is int})
        result = api.parse_response(payload)
    except api.AnalysisError as exc:
        print('analysis_error:', known(str(exc), ('response', 'chart', 'service', 'configuration', 'busy', 'image')))
        print('rejection:', exc.detail)
        raise SystemExit(1)
    print('parsed_direction:', known(result.get('direction'), ('BUY', 'SELL')))
    print('parsed_setup:', known(result.get('setup'), api.chart_strategy.SETUPS))


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print('SKIP: pass an approved chart-only image path to make one Gemini request')
    else:
        try:
            asyncio.run(main(sys.argv[1]))
        except Exception as exc:
            # Exception messages may contain URLs or user-provided content.
            raise SystemExit('Diagnostic failed: ' + type(exc).__name__)
