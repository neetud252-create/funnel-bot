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
import chart_progress
import config
import localization

INTRO = ('📸 <b>Try Pocket IQ chart analysis</b>\n\n'
         'Demo signals remaining: <b>{left} of 1</b>. No deposit is needed to try.\n\n'
         'Upload a clear chart screenshot to see the trend and momentum. '
         'AI predictions can be wrong. Results are not guaranteed.')
VERIFIED = ('🤖 <b>Pocket IQ AI Chart Analysis</b>\n\n'
            'Your account is verified. Tap Upload Chart to analyse your screenshot.')
EXHAUSTED = ('🔒 <b>Your demo signal has been used.</b>\n\n'
             'For more signals, tap Get Full Access and follow the account setup instructions.')
PROMPT = ('🔲 AI Chart Analyzer\n\n'
          '📊 Send your trading chart screenshot.\n'
          'AI will describe the chart and provide an uncertain directional interpretation.\n\n'
          '<i>Crop out personal details before sending. Timer shows the chart interval.</i>')
MESSAGES = {
    'configuration': 'Chart analysis is temporarily unavailable. Please try again later.',
    'service': 'Analysis could not be completed. Please try again shortly.',
    'response': 'Analysis could not be completed. Please try again shortly.',
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
DAILY_STATUS = ('\n\n⚡ <b>Signals</b>\n'
                '— Available today: {limit} signals\n'
                '— Used: {used}\n— Left: {left}')
SOURCES = (INTRO, VERIFIED, EXHAUSTED, PROMPT, RESULT, *MESSAGES.values(), *EXTRA,
           DAILY_STATUS, 'Remaining Signals: {left}', 'New Analysis', *chart_progress.SOURCES)
BACK = [[('🔙 Back', 'cb:home:back', 'success')]]
VERIFIED_BACK = [[('🔙 Back', 'cb:home:access', 'success')]]
HELP = [[('📖 Screenshot Guide', 'cb:flow:screenshot'), ('💬 Support', 'cb:flow:support')]]
UPLOAD = [[('📸 Upload Chart', 'cb:chart:upload', 'primary')], *HELP, *BACK]
NEW_ANALYSIS = [[('🔄 New Analysis', 'cb:chart:upload', 'primary')], *BACK]
ACCESS = [[('🚀 Get Full Access', 'cb:home:access', 'success')], *HELP, *BACK]
_inflight = set()


async def usage_text(tg_id):
    """Use the same persisted totals and tier as the main menu."""
    import db
    user = await db.get_user(tg_id)
    if not user or not user['verified']:
        return ''
    limit = config.daily_limit(user['is_premium'])
    used, left = await db.signal_state(tg_id, limit)
    language = await localization.language_for(tg_id)
    return localization.translate_parts(DAILY_STATUS, language).format(
        limit=limit, used=used, left=left)


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
    back = VERIFIED_BACK if verified else BACK
    usage = await usage_text(cb.from_user.id) if verified else ''
    if verified and not left:
        await render(bot, cb.from_user.id, None, MESSAGES['limit'] + usage, back, screen_key='chart_limit')
        return
    if not verified and not left:
        await render(bot, cb.from_user.id, None, EXHAUSTED, ACCESS, screen_key='chart_trial_exhausted')
        return
    if not upload:
        await render(bot, cb.from_user.id, None,
                     VERIFIED + usage if verified else INTRO.format(left=left),
                     UPLOAD[:1] + HELP + back, screen_key='chart_intro')
        return
    if not api.configured():
        await render(bot, cb.from_user.id, None, MESSAGES['configuration'], back, screen_key='chart_unavailable')
        return
    await state.set_state(Chart.waiting_image.state)
    await state.update_data(chart_session=secrets.token_hex(12), chart_verified=verified)
    await render(bot, cb.from_user.id, None, PROMPT + usage, HELP + back, screen_key='chart_upload')


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
    _inflight.add(tg_id)  # Publish before any await: one active analysis per user.
    session = None
    token = None
    progress = None
    consumed = delivery_attempted = False
    back = BACK
    async def active():
        return (await state.get_state() == Chart.waiting_image.state and
                (await state.get_data()).get('chart_session') == session)
    async def before_retry():
        if not await active():
            raise api.AnalysisError('cancelled')
        if progress is not None:
            progress.retrying = True
        blocked = await api.reserve(db.pool, tg_id, retry=True)
        if blocked:
            raise api.AnalysisError(blocked)
        if not await active():
            raise api.AnalysisError('cancelled')
    try:
        session = (await state.get_data()).get('chart_session')
        if (await state.get_data()).get('chart_verified'):
            back = VERIFIED_BACK
        if not session:
            return
        blocked, token = await trial.reserve(db.pool, tg_id, m.message_id)
        if blocked:
            if blocked == 'trial':
                await state.clear()
                await render(bot, tg_id, None, EXHAUSTED, ACCESS)
            elif blocked == 'limit':
                await state.clear()
                await render(bot, tg_id, None, MESSAGES['limit'] + await usage_text(tg_id), VERIFIED_BACK)
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
        loading = await render(bot, tg_id, None, chart_progress.frame(language), back, screen_key='chart_processing')
        progress = chart_progress.Progress(bot, tg_id, loading, language, active)
        progress.start()
        try:
            result = await api.analyse(data, language, m.caption or '', before_retry=before_retry)
        finally:
            # Await cancellation before any result/error render, so an old edit
            # cannot overwrite a finished result or a replacement upload session.
            await progress.stop()
        if await active():
            # Validate/render before consuming a slot: malformed provider data
            # must never be coerced into a direction or charged as a signal.
            text = chart_progress.completed(language) + result_text(result, language)
            if not await trial.consume(db.pool, token):
                verified, left = await trial.status(db.pool, tg_id)
                if verified and not left:
                    await state.clear()
                    await render(bot, tg_id, None, MESSAGES['limit'] + await usage_text(tg_id), VERIFIED_BACK)
                    return
                if not verified and not left:
                    await state.clear()
                    await render(bot, tg_id, None, EXHAUSTED, ACCESS)
                    return
                raise api.AnalysisError('service')
            consumed = True
            usage = await usage_text(tg_id)
            if usage:
                text += usage
                back = VERIFIED_BACK
            if not await active():
                return
            try:
                delivery_attempted = True
                result_rows = NEW_ANALYSIS[:1] + [[('📖 Understand This Result', 'cb:flow:explain')]]
                if not usage:
                    text += '\n\n' + localization.translate_parts(EXHAUSTED, language)
                    result_rows = ACCESS[:-1]
                await render(bot, tg_id, None, text, result_rows + back,
                             reply_to_message_id=m.message_id, raise_on_error=True, screen_key='chart_result')
            except (TelegramBadRequest, TelegramForbiddenError):
                # Telegram explicitly rejected the send, so no signal was delivered.
                await trial.release(db.pool, token, delivery_rejected=True)
                raise
    except api.AnalysisError as exc:
        code = str(exc)
        logging.warning('Chart analysis failed: code=%s detail=%s', code, exc.detail)
        if session and await active():
            await render(bot, tg_id, None, MESSAGES.get(code, MESSAGES['service']), NEW_ANALYSIS[:1] + back,
                         screen_key='chart_error')
    except Exception as exc:
        # Avoid logging image content, user captions, credentials or Telegram URLs.
        logging.warning('Chart request failed: type=%s', type(exc).__name__)
        if session and await active():
            await render(bot, tg_id, None, MESSAGES['service'], back, screen_key='chart_error')
    finally:
        try:
            if progress is not None:
                await progress.stop()
            if consumed and not delivery_attempted:
                await trial.release(db.pool, token, delivery_rejected=True)
            else:
                await trial.release(db.pool, token)  # Keep successful/uncertain sends counted.
        finally:
            _inflight.discard(tg_id)


def install(dp, render):
    async def handle(m, bot, state):
        await receive(m, bot, state, render)
    dp.message.register(handle, Chart.waiting_image)
    async def upload(cb, bot, state):
        await open_screen(cb, bot, state, render, upload=True)
    dp.callback_query.register(upload, F.data == 'chart:upload')
