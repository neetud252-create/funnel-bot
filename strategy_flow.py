"""Telegram YouTube import, transcript fallback, review and activation."""
import html
import logging
import secrets

from aiogram import F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import State, StatesGroup

import chart_analysis as api
import chart_signals as signals
import localization
import youtube_strategy as strategies

PROMPT = ('📺 <b>Make Strategy</b>\n\n'
          'Send a YouTube trading strategy video link. The bot reads its captions and extracts entry rules for your chart screenshots.')
FALLBACK = ('Captions could not be read. Paste the transcript in messages or send a UTF-8 .txt file, then tap Generate Strategy.')
MESSAGES = {
    'video_url': 'Send one valid YouTube video link.',
    'transcript': FALLBACK,
    'transcript_size': 'The transcript must contain at most 60,000 characters. Try a shorter video or transcript.',
    'strategy_rules': 'No complete chart strategy could be extracted. Try a video with clear entry rules.',
    'strategy_saved': 'The saved strategy could not be loaded. Import it again or use Default Strategy.',
    'working': '⏳ Reading the transcript and building your strategy…',
    'received': 'Transcript received. Add more text or tap Generate Strategy.',
    'expired': 'This strategy preview has expired. Open Make Strategy again.',
}
LABELS = ('Make Strategy', 'Default Strategy', 'Generate Strategy', 'Use Strategy',
          'Strategy Preview', 'Active Strategy', 'Requirements', 'BUY rules', 'SELL rules',
          'Avoid entries', 'Limitations', 'Source video', 'Pasted transcript', 'Video captions',
          'No rules supplied', 'Strategy: {name}',
          'Review the rules, then tap Use Strategy. Accuracy has not been verified.')
SOURCES = (PROMPT, *MESSAGES.values(), *LABELS)


class Strategy(StatesGroup):
    link = State()
    transcript = State()
    preview = State()


def tr(text, language):
    return localization.translate_parts(text, language)


def keyboard(*, build=False, preview=None):
    rows = []
    if preview:
        rows.append([('✅ Use Strategy', 'cb:strategy:use:' + preview, 'success')])
    if build:
        rows.append([('⚙️ Generate Strategy', 'cb:strategy:build', 'primary')])
    rows.extend([[('📊 Get Signals', 'cb:market:open', 'primary')],
                 [('📸 Upload Chart', 'cb:strategy:upload', 'primary')],
                 [('↩️ Default Strategy', 'cb:strategy:default', 'primary')], *signals.BACK])
    return rows


def summary(saved, language, *, active=False):
    rules = saved['rules']
    lines = ['📺 <b>' + tr('Active Strategy' if active else 'Strategy Preview', language) + '</b>',
             '<b>' + html.escape(rules['name']) + '</b>', html.escape(rules['summary'])]
    for field, label in (('requirements', 'Requirements'), ('buy_rules', 'BUY rules'),
                         ('sell_rules', 'SELL rules'), ('avoid_rules', 'Avoid entries')):
        lines.append('\n<b>' + tr(label, language) + '</b>')
        lines.extend('• ' + html.escape(rule['condition']) for rule in rules[field])
        if not rules[field]:
            lines.append(tr('No rules supplied', language))
    lines.extend(['\n<b>' + tr('Limitations', language) + '</b>', html.escape(rules['limitations']),
                  '<a href="https://www.youtube.com/watch?v=' + saved['video_id'] + '">' +
                  tr('Source video', language) + '</a> · ' +
                  tr('Pasted transcript' if saved.get('origin') == 'pasted' else 'Video captions', language)])
    if not active:
        lines.append('\n' + tr('Review the rules, then tap Use Strategy. Accuracy has not been verified.', language))
    return '\n'.join(lines)


async def open_screen(event, bot, state, render, *, callback=True):
    message = event.message if callback else event
    if message.chat.type != 'private':
        if callback:
            await event.answer(signals.MESSAGES['private'], show_alert=True)
        return
    if callback:
        await event.answer()
    uid = event.from_user.id
    await state.clear()
    await state.set_state(Strategy.link)
    await state.update_data(strategy_session=secrets.token_hex(12))
    language = await localization.language_for(uid)
    text = tr(PROMPT, language)
    try:
        saved = await strategies.get_active(uid)
        if saved:
            text = summary(saved, language, active=True) + '\n\n' + text
    except api.AnalysisError as exc:
        text = tr(MESSAGES[str(exc)], language) + '\n\n' + text
    await render(bot, uid, None, text, keyboard(), screen_key='strategy_import')


async def build(event, bot, state, render, *, video=None, transcript=None):
    import db
    uid = event.from_user.id
    reply = event.message.answer if hasattr(event, 'message') else event.answer
    if uid in signals._inflight or len(signals._inflight) >= api.MAX_CONCURRENT:
        await reply(signals.MESSAGES['busy'])
        return
    if not api.configured():
        await reply(signals.MESSAGES['configuration'])
        return
    signals._inflight.add(uid)
    session = None
    async def active():
        return ((await state.get_data()).get('strategy_session') == session and
                await state.get_state() in (Strategy.link.state, Strategy.transcript.state))
    async def reserve(*, retry=False):
        if not await active():
            raise api.AnalysisError('cancelled')
        blocked = await api.reserve(db.pool, uid, retry=retry)
        if blocked:
            raise api.AnalysisError(blocked)
        if not await active():
            raise api.AnalysisError('cancelled')
    async def retry():
        await reserve(retry=True)
    try:
        session = (await state.get_data()).get('strategy_session')
        if not session or not await active():
            return
        language = await localization.language_for(uid)
        # Reserve before network access; failed imports share the durable abuse
        # budget but never consume a delivered-signal slot.
        await reserve()
        await render(bot, uid, None, tr(MESSAGES['working'], language), signals.BACK, screen_key='strategy_processing')
        if not await active():
            return
        origin = 'pasted' if transcript is not None else 'captions'
        if transcript is None:
            await state.update_data(strategy_video=video)
            transcript = await strategies.fetch_transcript(video, language)
        if not await active():
            return
        saved = await strategies.generate(transcript, video, language, before_retry=retry, origin=origin)
        if not await active():
            return
        token = secrets.token_hex(8)
        await state.set_state(Strategy.preview)
        await state.set_data({'strategy_session': session, 'strategy_preview': saved, 'strategy_token': token})
        await render(bot, uid, None, summary(saved, language), keyboard(preview=token), screen_key='strategy_preview')
    except api.AnalysisError as exc:
        if await active():
            if str(exc) in ('transcript', 'transcript_size'):
                await state.set_state(Strategy.transcript)
            language = await localization.language_for(uid)
            text = MESSAGES.get(str(exc), signals.MESSAGES.get(str(exc), signals.MESSAGES['service']))
            await render(bot, uid, None, tr(text, language),
                         keyboard(build=await state.get_state() == Strategy.transcript.state), screen_key='strategy_error')
    except Exception as exc:
        logging.warning('Strategy import failed: type=%s', type(exc).__name__)
        if await active():
            await render(bot, uid, None, signals.MESSAGES['service'], keyboard())
    finally:
        signals._inflight.discard(uid)


async def receive(m, bot, state, render):
    if m.chat.type != 'private':
        return
    if m.from_user.id in signals._inflight:
        await m.answer(signals.MESSAGES['busy'])
        return
    current = await state.get_state()
    if current == Strategy.transcript.state and m.text:
        try:
            strategies.video_id(m.text)
        except api.AnalysisError:
            pass
        else:
            current = None  # A new link replaces the unfinished transcript.
    if current != Strategy.transcript.state:
        try:
            video = strategies.video_id(m.text)
        except api.AnalysisError:
            await m.answer(MESSAGES['video_url'])
            return
        if current != Strategy.link.state:
            await state.clear()
            await state.set_state(Strategy.link)
            await state.update_data(strategy_session=secrets.token_hex(12))
        await build(m, bot, state, render, video=video)
        return
    if m.media_group_id:
        await m.answer(FALLBACK)
        return
    text = m.text or ''
    if m.document:
        if not (m.document.file_name or '').lower().endswith('.txt'):
            await m.answer(FALLBACK)
            return
        if not m.document.file_size or m.document.file_size > strategies.MAX_TRANSCRIPT_BYTES:
            await m.answer(MESSAGES['transcript_size'])
            return
        import asyncio
        import io
        class TextBuffer(io.BytesIO):
            def write(self, chunk):
                if self.tell() + len(chunk) > strategies.MAX_TRANSCRIPT_BYTES:
                    raise ValueError()
                return super().write(chunk)
        session = (await state.get_data()).get('strategy_session')
        try:
            with TextBuffer() as destination:
                async with asyncio.timeout(15):
                    await bot.download(m.document, destination=destination, timeout=10)
                text = destination.getvalue().decode('utf-8-sig')
        except Exception:
            await m.answer(FALLBACK)
            return
        if ((await state.get_data()).get('strategy_session') != session or
                await state.get_state() != Strategy.transcript.state):
            return
    if not text.strip():
        await m.answer(FALLBACK)
        return
    data = await state.get_data()
    text = (data.get('strategy_transcript', '') + '\n' + text).strip()
    if len(text) > strategies.MAX_TRANSCRIPT_CHARS:
        await m.answer(MESSAGES['transcript_size'])
        return
    await state.update_data(strategy_transcript=text)
    await render(bot, m.from_user.id, None, MESSAGES['received'], keyboard(build=True))


async def action(cb, bot, state, render):
    if cb.message.chat.type != 'private':
        await cb.answer(signals.MESSAGES['private'], show_alert=True)
        return
    if cb.data == 'strategy:open':
        await open_screen(cb, bot, state, render)
        return
    if cb.data == 'strategy:upload':
        await signals.open_screen(cb, bot, state, render, upload=True)
        return
    if cb.from_user.id in signals._inflight:
        await cb.answer(signals.MESSAGES['busy'], show_alert=True)
        return
    data = await state.get_data()
    if cb.data == 'strategy:default':
        await save_and_upload(cb, bot, state, render, None)
    elif cb.data.startswith('strategy:use:'):
        if (await state.get_state() != Strategy.preview.state or
                cb.data != 'strategy:use:' + data.get('strategy_token', '') or not data.get('strategy_preview')):
            await cb.answer(MESSAGES['expired'], show_alert=True)
            return
        await save_and_upload(cb, bot, state, render, data['strategy_preview'])
    elif cb.data == 'strategy:build':
        if await state.get_state() != Strategy.transcript.state or not data.get('strategy_transcript'):
            await cb.answer(FALLBACK, show_alert=True)
            return
        await cb.answer()
        await build(cb, bot, state, render, video=data['strategy_video'], transcript=data['strategy_transcript'])


async def save_and_upload(cb, bot, state, render, saved):
    import db
    uid = cb.from_user.id
    if uid in signals._inflight:
        await cb.answer(signals.MESSAGES['busy'], show_alert=True)
        return
    signals._inflight.add(uid)
    try:
        await state.update_data(strategy_saving=secrets.token_hex(8))
        before = await state.get_data()
        await db.set_chart_strategy(uid, saved)
        if await state.get_data() != before:
            await cb.answer()
            return
        import market_data
        if market_data.enabled(uid):
            import market_signals
            await market_signals.open_screen(cb, bot, state, render)
        else:
            await signals.open_screen(cb, bot, state, render, upload=True)
    except Exception as exc:
        logging.warning('Strategy save failed: type=%s', type(exc).__name__)
        await cb.answer(signals.MESSAGES['service'], show_alert=True)
    finally:
        signals._inflight.discard(uid)


def install(dp, render):
    async def callback(cb, bot, state):
        await action(cb, bot, state, render)
    async def command(m, bot, state):
        await open_screen(m, bot, state, render, callback=False)
    async def message(m, bot, state):
        await receive(m, bot, state, render)
    async def screenshot(m, bot, state):
        await signals.receive(m, bot, state, render)
    dp.callback_query.register(callback, F.data.startswith('strategy:'))
    dp.message.register(command, Command('strategy'))
    dp.message.register(message, StateFilter(Strategy.link, Strategy.transcript, Strategy.preview))
    # Pasting a link from home or the chart uploader also opens the importer.
    dp.message.register(message, StateFilter(None, signals.Chart.waiting_image, 'Market:browsing'),
                        F.text.regexp(r'(?i)^(?:https?://)?(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)/'))
    dp.message.register(screenshot, signals.Chart.waiting_image)
