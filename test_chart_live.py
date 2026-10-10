"""Opt-in provider diagnostic: one paid request, no bot/database changes.

Run: python test_chart_live.py path/to/chart.png
Requires DEEPSEEK_API_KEY. Logs only bounded metadata, never image/model text.
"""
import asyncio
import os
import sys
from pathlib import Path

import httpx
import chart_analysis as api


def known(value, values):
    return value if isinstance(value, str) and value in values else 'other'


async def main(path):
    if not api.configured():
        raise SystemExit('DEEPSEEK_API_KEY is not configured')
    data = Path(path).read_bytes()
    body = api.request_body(data, 'en')
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post('https://api.deepseek.com/responses',
            headers={'Authorization': 'Bearer ' + os.environ['DEEPSEEK_API_KEY'].strip()}, json=body)
    print('http_status:', response.status_code)
    if response.status_code != 200:
        return
    payload = response.json()
    print('status:', known(payload.get('status'), ('completed', 'incomplete', 'failed', 'in_progress')))
    details = payload.get('incomplete_details') or {}
    print('incomplete_reason:', known(details.get('reason'), ('max_output_tokens', 'content_filter')))
    usage = payload.get('usage') or {}
    print('usage:', {k: v for k, v in usage.items()
                     if k in ('input_tokens', 'output_tokens', 'total_tokens') and type(v) is int})
    messages = [item for item in payload.get('output', []) if item.get('type') == 'message']
    print('message_count:', len(messages))
    print('message_statuses:', [known(m.get('status'), ('completed', 'incomplete', 'in_progress')) for m in messages])
    try:
        result = api.parse_response(payload)
    except api.AnalysisError as exc:
        print('analysis_error:', known(str(exc), ('response', 'chart', 'service', 'configuration')))
        print('rejection:', exc.detail)
        return
    print('parsed_direction:', known(result.get('direction'), ('BUY', 'SELL')))
    print('parsed_setup:', known(result.get('setup'), api.chart_strategy.SETUPS))


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print('SKIP: pass an image path to make one paid provider request')
    else:
        try:
            asyncio.run(main(sys.argv[1]))
        except Exception as exc:
            # Exception messages may contain URLs or user-provided content.
            raise SystemExit('Diagnostic failed: ' + type(exc).__name__)
