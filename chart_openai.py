"""OpenAI Responses adapter; shares the existing chart strategy and validation."""
import chart_analysis as api


def request_body(gemini_body, model):
    parts = gemini_body['contents'][0]['parts']
    content = []
    for part in parts:
        if 'text' in part:
            content.append({'type': 'input_text', 'text': part['text']})
        else:
            picture = part['inlineData']
            content.append({'type': 'input_image', 'detail': 'high',
                            'image_url': 'data:' + picture['mimeType'] + ';base64,' + picture['data']})
    return {
        'model': model,
        'store': False,
        'instructions': gemini_body['systemInstruction']['parts'][0]['text'],
        'input': [{'role': 'user', 'content': content}],
        'reasoning': {'effort': 'low'},
        'max_output_tokens': api.MAX_OUTPUT_TOKENS,
        'text': {'format': {'type': 'json_schema', 'name': 'chart_analysis',
                            'strict': True, 'schema': gemini_body['generationConfig']['responseJsonSchema']}},
    }


def parse_response(payload, *, validator=None):
    detail = 'envelope'
    try:
        if not isinstance(payload, dict):
            raise ValueError()
        detail = 'incomplete'
        if payload.get('status') != 'completed' or payload.get('error') or payload.get('incomplete_details'):
            raise ValueError()
        detail = 'message_content'
        outputs = payload['output']
        if not isinstance(outputs, list) or any(not isinstance(item, dict) for item in outputs):
            raise ValueError()
        # Reasoning items are metadata, never a signal. Tool output is unexpected.
        if any(item.get('type') not in ('message', 'reasoning') for item in outputs):
            raise ValueError()
        messages = [item for item in outputs if item.get('type') == 'message']
        if len(messages) != 1:
            raise ValueError()
        message = messages[0]
        if message.get('role') != 'assistant' or message.get('status') != 'completed':
            raise ValueError()
        parts = message['content']
        if not isinstance(parts, list) or len(parts) != 1:
            raise ValueError()
        part = parts[0]
        if part.get('type') != 'output_text' or not isinstance(part.get('text'), str):
            raise ValueError()
        detail = 'json'
        result = api._result_json(part['text'])
        return (validator or api.validate_result)(result)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise api.AnalysisError('response', detail) from exc
