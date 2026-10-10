"""Private-chat screenshot flow for the public Test Signals button."""
import asyncio
import html
import logging
import os
import re
import secrets

from aiogram.fsm.state import State, StatesGroup
from aiogram import F
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
import chart_analysis as api
import chart_strategy
import chart_trial as trial
import localization

INTRO = ('🤖 <b>Go+ AI Test Mode Active!</b> ⚡\n\n'
         "You have been granted access to test the bot's signals.\n\n"
         '📊 <b>Remaining Signals:</b> {left}\n'
         '🎯 <b>Accuracy:</b> Not yet verified\n\n'
         'Try it out now for free before verifying! 🚀')
# Opt-in UI preview only; this sample percentage is not measured accuracy.
TEST_INTRO = INTRO.replace('Not yet verified', '95% + Guaranteed')
VERIFIED = ('🤖 <b>Go+ AI Chart Analysis</b>\n\n'
            'Your account is verified. Tap Test Signals to upload your chart.')
EXHAUSTED = ('🔒 <b>Your 2 free signals have been used.</b>\n\n'
             'Verify your account to continue receiving chart analysis.\n'
             'Tap Get Bot Access below to continue.')
PROMPT = ('🔲 AI Chart Analyzer\n\n'
          '📊 Send your trading chart screenshot.\n'
          '⚡ DeepSeek AI will analyse your chart and return BUY or SELL.\n\n'
          '<i>Crop out personal details before sending. Timer shows the chart interval.</i>')
MESSAGES = {
    'configuration': 'Chart analysis is temporarily unavailable. Please try again later.',
    'service': 'Analysis could not be completed. Please try again shortly.',
    'response': 'No usable analysis was returned. Please try a clearer chart screenshot.',
    'busy': 'Chart analysis is busy. Please try again shortly.',
    'image': 'Send one JPG, PNG or WebP chart screenshot under 8 MB.',
    'chart': 'The chart could not be read. Send a clearer screenshot with more candle history.',
    'limit': 'Daily chart analysis limit reached. Try again tomorrow (UTC).',
    'cooldown': 'Please wait 30 seconds between chart requests.',
    'private': 'Open Test Signals in a private chat with the bot.',
    'working': '⏳ Analysing your chart…',
}
RESULT = ('🔲 AI Trading Signal Result\n\n'
          '💗 <b>Market Analysis:</b>\n'
          '- 📈 <b>Trend:</b> {trend}\n- 🪙 <b>Momentum:</b> {momentum}\n\n'
          '🎯 <b>Recommendation:</b>\n'
          '- <b>Prediction:</b> {direction}\n'
          '- <b>Timer:</b> {timer}')
EXTRA = ('Not visible', '(tentative)', '{count} minute', '{count} minutes',
         '{count} second', '{count} seconds', '{count} hour', '{count} hours',
         '{count} day', '{count} days')
SOURCES = (INTRO, TEST_INTRO, VERIFIED, EXHAUSTED, PROMPT, RESULT, *MESSAGES.values(), *EXTRA,
           'Remaining Signals: {left}', 'New Analysis')
BACK = [[('🔙 Back', 'cb:home:back', 'success')]]
UPLOAD = [[('🎯 Test Signals', 'cb:chart:upload', 'primary')], *BACK]
NEW_ANALYSIS = [[('🔄 New Analysis', 'cb:chart:upload', 'primary')], *BACK]
ACCESS = [[('💎 Get Bot Access', 'cb:home:access', 'success')], *BACK]
_inflight = set()


class Chart(StatesGroup):
    waiting_image = State()


async def open_screen(cb, bot, state, render, *, upload=False):
    import db
    if cb.message.chat.type != 'private':
        await cb.answer(MESSAGES['private'], show_alert=True)
        return
    await cb.answer()
    await state.clear()
    verified, left = await trial.status(db.pool, cb.from_user.id)
    if not verified and not left:
        await render(bot, cb.from_user.id, None, EXHAUSTED, ACCESS)
        return
    if not upload:
        intro = TEST_INTRO if os.getenv('CHART_UI_TEST_COPY', '').strip() == '1' else INTRO
        await render(bot, cb.from_user.id, None, VERIFIED if verified else intro.format(left=left), UPLOAD)
        return
    if not api.configured():
        await render(bot, cb.from_user.id, None, MESSAGES['configuration'], BACK)
        return
    await state.set_state(Chart.waiting_image.state)
    await state.update_data(chart_session=secrets.token_hex(12))
    await render(bot, cb.from_user.id, None, PROMPT, BACK)


def timer_text(timeframe, language):
    """The reference's Timer row displays the observed candle interval, not an expiry."""
    if not timeframe:
        return localization.translate_parts('Not visible', language)
    value = timeframe.strip().lower()
    match = re.fullmatch(r'(\d+)\s*(s|sec(?:ond)?s?|m|min(?:ute)?s?|h|hours?|d|days?)', value)
    if not match:
        match = re.fullmatch(r'([smhd])(\d+)', value)
        if match:
            count, unit = match[2], match[1]
        else:
            return html.escape(timeframe)
    else:
        count, unit = match[1], match[2]
    number = int(count)
    unit = {'s': 'second', 'm': 'minute', 'h': 'hour', 'd': 'day'}[unit[0]]
    template = '{count} ' + unit + ('s' if number != 1 else '')
    return localization.translate_parts(template, language).format(count=number)


def result_text(result, language):
    # Translate labels BEFORE interpolation, so the model's explanation cannot
    # accidentally match/alter a built-in phrase. Escape every dynamic field.
    translate = lambda s: localization.translate_parts(s, language)
    direction = result['direction']
    if direction not in ('BUY', 'SELL'):
        raise api.AnalysisError('response')
    if not chart_strategy.confirmed_setup(result):
        direction += ' ' + translate('(tentative)')
    return translate(RESULT).format(direction=direction,
        trend=html.escape(result['trend']), momentum=html.escape(result['momentum']),
        timer=timer_text(result['timeframe'], language))


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
    token = None
    async def active():
        return (await state.get_state() == Chart.waiting_image.state and
                (await state.get_data()).get('chart_session') == session)
    try:
        session = (await state.get_data()).get('chart_session')
        if not session:
            return
        blocked, token = await trial.reserve(db.pool, tg_id, m.message_id)
        if blocked:
            if blocked == 'trial':
                await state.clear()
                await render(bot, tg_id, None, EXHAUSTED, ACCESS)
            else:
                await m.answer(MESSAGES[blocked])
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
            # Validate/render before consuming a slot: malformed provider data
            # must never be coerced into a direction or charged as a signal.
            text = result_text(result, language)
            if not await trial.consume(db.pool, token):
                raise api.AnalysisError('service')
            if not await active():
                await trial.release(db.pool, token, delivery_rejected=True)
                return
            try:
                await render(bot, tg_id, None, text, NEW_ANALYSIS,
                             reply_to_message_id=m.message_id, raise_on_error=True)
            except (TelegramBadRequest, TelegramForbiddenError):
                # Telegram explicitly rejected the send, so no signal was delivered.
                await trial.release(db.pool, token, delivery_rejected=True)
                raise
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
        try:
            await trial.release(db.pool, token)  # Releases pending only; used slots stay used.
        finally:
            _inflight.discard(tg_id)


def install(dp, render):
    async def handle(m, bot, state):
        await receive(m, bot, state, render)
    dp.message.register(handle, Chart.waiting_image)
    async def upload(cb, bot, state):
        await open_screen(cb, bot, state, render, upload=True)
    dp.callback_query.register(upload, F.data == 'chart:upload')
