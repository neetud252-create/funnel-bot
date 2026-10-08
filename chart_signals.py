"""Private-chat screenshot flow for the public Test Signals button."""
import asyncio
import html
import logging
import secrets

from aiogram.fsm.state import State, StatesGroup
import chart_analysis as api
import chart_strategy
import localization

PROMPT = ('🎯 <b>Test Signals — Chart Analysis</b>\n\n'
          'Send a clear screenshot of any trading chart. Include the latest candles, pair name and timeframe.\n\n'
          'Analysis follows the level-reaction strategy: support, resistance and candle confirmation.\n\n'
          'You will receive a BUY or SELL bias with reasons, or WAIT when the chart is unclear.\n\n'
          'Crop out personal details. Your image is sent to Google Gemini for analysis.\n'
          'Free-tier submissions may be used by Google to improve its products.\n'
          'Screenshot analysis is not a live price feed or a guaranteed prediction.')
MESSAGES = {
    'configuration': 'Chart analysis is temporarily unavailable. Please try again later.',
    'service': 'Analysis could not be completed. Please try again shortly.',
    'response': 'No usable analysis was returned. Please try a clearer chart screenshot.',
    'busy': 'Chart analysis is busy. Please try again shortly.',
    'image': 'Send one JPG, PNG or WebP chart screenshot under 8 MB.',
    'limit': 'Daily chart analysis limit reached. Try again tomorrow (UTC).',
    'cooldown': 'Please wait 30 seconds between chart requests.',
    'private': 'Open Test Signals in a private chat with the bot.',
    'working': '⏳ Analysing your chart…',
}
RESULT = ('🎯 <b>Chart Analysis</b>\n\n<b>{direction}</b>\n'
          'Pair: <b>{asset}</b>\nChart timeframe: <b>{timeframe}</b>\n\n'
          '{reason}\n\n{invalidation}\n\n'
          'Based on your screenshot only. Prices may have changed; no outcome is guaranteed.')
EXTRA = ('Not visible', 'Invalidation: {condition}', 'WAIT — No clear setup')
SOURCES = (PROMPT, RESULT, *MESSAGES.values(), *EXTRA, chart_strategy.WAIT_REASON)
BACK = [[('⬅️ Back', 'cb:home:back', 'success')]]
_inflight = set()


class Chart(StatesGroup):
    waiting_image = State()


async def open_screen(cb, bot, state, render):
    if cb.message.chat.type != 'private':
        await cb.answer(MESSAGES['private'], show_alert=True)
        return
    await cb.answer()
    await state.clear()
    if not api.configured():
        await render(bot, cb.from_user.id, None, MESSAGES['configuration'], BACK)
        return
    await state.set_state(Chart.waiting_image.state)
    await state.update_data(chart_session=secrets.token_hex(12))
    await render(bot, cb.from_user.id, None, PROMPT, BACK)


def result_text(result, language):
    # Translate labels BEFORE interpolation, so the model's explanation cannot
    # accidentally match/alter a built-in phrase. Escape every dynamic field.
    translate = lambda s: localization.translate_parts(s, language)
    direction = result['direction']
    direction = '🟢 BUY' if direction == 'BUY' else '🔴 SELL' if direction == 'SELL' else '⏸ ' + translate(EXTRA[2])
    condition = result['invalidation'] if result['direction'] != 'WAIT' else None
    invalidation = translate(EXTRA[1]).format(condition=html.escape(condition)) if condition else ''
    reason = (translate(chart_strategy.WAIT_REASON) if result['reason'] == chart_strategy.WAIT_REASON
              else result['reason'])
    return translate(RESULT).format(direction=direction,
        asset=html.escape(result['asset'] or translate('Not visible')),
        timeframe=html.escape(result['timeframe'] or translate('Not visible')),
        reason=html.escape(reason), invalidation=invalidation)


async def receive(m, bot, state, render):
    import db
    tg_id = m.from_user.id
    if m.chat.type != 'private':
        return
    if tg_id in _inflight or len(_inflight) >= api.MAX_CONCURRENT:
        await m.answer(MESSAGES['busy'])
        return
    if m.media_group_id:
        await m.answer(MESSAGES['image'])
        return
    media = m.photo[-1] if m.photo else m.document
    if (not media or (media.file_size or 0) > api.MAX_IMAGE_BYTES
            or (m.document and m.document.mime_type not in ('image/png', 'image/jpeg', 'image/webp'))):
        await m.answer(MESSAGES['image'])
        return
    if not api.configured():
        await m.answer(MESSAGES['configuration'])
        return
    _inflight.add(tg_id)  # Publish before any await: one paid request per user.
    session = None
    async def active():
        return (await state.get_state() == Chart.waiting_image.state and
                (await state.get_data()).get('chart_session') == session)
    try:
        session = (await state.get_data()).get('chart_session')
        if not session:
            return
        with api.ImageBuffer() as image:
            async with asyncio.timeout(15):
                await bot.download(media, destination=image, timeout=10)
            data = image.getvalue()
        api.image_type(data)
        if not await active():
            return
        language = await localization.language_for(tg_id)
        blocked = await api.reserve(db.pool, tg_id)
        if blocked:
            if await active():
                await m.answer(MESSAGES[blocked])
            return
        if not await active():
            return
        await render(bot, tg_id, None, MESSAGES['working'], BACK)
        result = await api.analyse(data, language, m.caption or '')
        if await active():
            await render(bot, tg_id, None, result_text(result, language),
                         [[('🎯 Test Signals', 'cb:home:test', 'primary')], *BACK])
    except api.AnalysisError as exc:
        code = str(exc)
        logging.warning('Chart analysis failed: code=%s', code)
        if session and await active():
            await render(bot, tg_id, None, MESSAGES.get(code, MESSAGES['service']), BACK)
    except Exception as exc:
        # Avoid logging image content, user captions, credentials or Telegram URLs.
        logging.warning('Chart request failed: type=%s', type(exc).__name__)
        if session and await active():
            await render(bot, tg_id, None, MESSAGES['service'], BACK)
    finally:
        _inflight.discard(tg_id)


def install(dp, render):
    async def handle(m, bot, state):
        await receive(m, bot, state, render)
    dp.message.register(handle, Chart.waiting_image)
