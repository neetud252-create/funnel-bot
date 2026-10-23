"""Asset picker -> validated candles -> generated chart -> existing AI analysis."""
import asyncio
from datetime import datetime, timezone
import html
import logging
import secrets

from aiogram import F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

import chart_analysis as api
import chart_progress
import chart_signals
import chart_strategy
import chart_trial as trial
import localization
import market_chart
import market_data as data

PAGE_SIZE = 10
BACK = [[('⬅️ Home', 'cb:home:back')]]
RETRY = [[('📊 Choose Asset', 'cb:market:open', 'primary')],
         [('📸 Upload a Screenshot', 'cb:chart:upload')], *BACK]
MESSAGES = {
    'configuration': 'Asset analysis is being connected. You can still upload a chart screenshot.',
    'plan': 'This asset feed is not available on the connected data plan. Please try screenshot analysis.',
    'quota': 'The market-data allowance is currently exhausted. Please try again later or upload a screenshot.',
    'asset': 'This asset is no longer available. Please choose another asset.',
    'empty': 'No assets are available from the connected feed right now.',
    'closed': 'This asset is currently closed for trading. Please choose another asset.',
    'stale': 'Fresh candles are not available for this asset yet. Please try again shortly.',
    'gaps': 'The candle history has missing data. Please try another asset or try again later.',
    'history': 'There are not enough completed candles to analyse this asset yet.',
    'invalid': 'The candle data could not be verified. No analysis was used. Please try again later.',
    'service': 'Market data is temporarily unavailable. Please try again shortly.',
}


class Market(StatesGroup):
    browsing = State()


def filtered(assets, query):
    query = query.casefold().replace('/', '').replace(' ', '')
    return [a for a in assets if query in (a.symbol + a.name).casefold().replace('/', '').replace(' ', '')]


def picker(assets, period, page=0, query=''):
    matches = filtered(assets, query)
    pages = max(1, (len(matches) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages-1))
    text = ('📊 <b>Choose your asset</b>\n\n'
            f'Pocket Option OTC · <b>{period//60}-minute candles</b>\n'
            f'{len(matches)} assets on the connected data plan · Page {page+1}/{pages}\n\n'
            'Tap an asset for its candlestick chart and analysis.\n'
            'Type a name or pair to search, e.g. EURUSD or Apple.')
    if query:
        text += '\n\nSearch: <b>' + html.escape(query) + '</b>'
    if not matches:
        text += '\nNo matching assets. Try another name.'
    rows = [[('✓ 1 Minute' if period == 60 else '1 Minute', 'cb:market:tf:60', 'primary'),
             ('✓ 5 Minutes' if period == 300 else '5 Minutes', 'cb:market:tf:300', 'primary')]]
    buttons = [(('⛔ ' if a.open is False else '') + a.name[:40], 'cb:market:a:' + a.key)
               for a in matches[page*PAGE_SIZE:(page+1)*PAGE_SIZE]]
    rows += [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    nav = []
    if page:
        nav.append(('◀ Previous', f'cb:market:p:{page-1}'))
    if page+1 < pages:
        nav.append(('Next ▶', f'cb:market:p:{page+1}'))
    if nav:
        rows.append(nav)
    if query:
        rows.append([('All Assets', 'cb:market:all')])
    rows += [[('📸 Upload a Screenshot', 'cb:chart:upload')], *BACK]
    return text, rows


async def allowed(tg_id, bot, render, *, check_allowance=True):
    import db
    if not data.enabled(tg_id):
        await render(bot, tg_id, None, MESSAGES['configuration'], RETRY, screen_key='market_unavailable')
        return False
    # Browsing the catalogue does not spend an analysis. Enforce the shared
    # allowance when an asset is selected, not when opening or searching it.
    if not check_allowance:
        return True
    verified, left = await trial.status(db.pool, tg_id)
    if not left:
        text = chart_signals.MESSAGES['limit'] if verified else chart_signals.EXHAUSTED
        await render(bot, tg_id, None, text, chart_signals.ACCESS, screen_key='market_limit')
        return False
    return True


async def show_picker(bot, tg_id, state, render, page=0):
    saved = await state.get_data()
    session = saved.get('market_session')
    assets = await data.feed().assets()
    if (await state.get_data()).get('market_session') != session or await state.get_state() != Market.browsing.state:
        return
    text, rows = picker(assets, saved.get('market_period', 60), page, saved.get('market_query', ''))
    await render(bot, tg_id, None, text, rows, screen_key='market_assets')


async def open_screen(cb, bot, state, render):
    if cb.message.chat.type != 'private':
        await cb.answer('Open the bot in a private chat.', show_alert=True)
        return
    await cb.answer()
    await open_picker(bot, cb.from_user.id, state, render)


async def open_picker(bot, tg_id, state, render):
    await state.clear()
    if not await allowed(tg_id, bot, render, check_allowance=False):
        return
    await state.set_state(Market.browsing.state)
    await state.update_data(market_session=secrets.token_hex(12), market_period=60, market_query='')
    try:
        await show_picker(bot, tg_id, state, render)
    except data.FeedError as exc:
        await render(bot, tg_id, None, MESSAGES.get(str(exc), MESSAGES['service']), RETRY, screen_key='market_error')


def result_text(snapshot, result):
    # A weaker directional bias isn't a confirmed entry. Never force a signal
    # just because the upstream image-analysis contract returns BUY or SELL.
    confirmed = chart_strategy.confirmed_setup(result)
    direction = result.get('direction')
    if direction not in ('BUY', 'SELL'):
        raise api.AnalysisError('response')
    status = ('🟢 BUY bias' if direction == 'BUY' else '🔴 SELL bias') if confirmed else '⏸ WAIT — no confirmed setup'
    safe = lambda value: html.escape(str(value)[:180])
    closed = datetime.fromtimestamp(snapshot.closed_at, timezone.utc).strftime('%d %b %H:%M UTC')
    return (f'📊 <b>{html.escape(snapshot.asset.name)} · M{snapshot.period//60}</b>\n\n'
            f'<b>{status}</b>\n'
            f'<b>Trend:</b> {safe(result.get("trend", ""))}\n'
            f'<b>Momentum:</b> {safe(result.get("momentum", ""))}\n\n'
            f'<b>Last close:</b> {snapshot.candles[-1].close:.8g}\n'
            f'<b>Candle closed:</b> {closed}\n'
            '<i>OTCharts data · AI analysis of completed candles. Predictions can be wrong.</i>')


async def analyse(cb, bot, state, render, asset_key):
    tg_id = cb.from_user.id
    # Shared with uploads: changing input method cannot bypass concurrency limits.
    if tg_id in chart_signals._inflight or len(chart_signals._inflight) >= api.MAX_CONCURRENT:
        await cb.answer(chart_signals.MESSAGES['busy'], show_alert=True)
        return
    # Claim before the first await, including the callback acknowledgement.
    # Otherwise two fast taps can both pass the check and replace each other's
    # session while reserving two allowances.
    chart_signals._inflight.add(tg_id)
    try:
        await _analyse(cb, bot, state, render, asset_key)
    finally:
        chart_signals._inflight.discard(tg_id)


async def _analyse(cb, bot, state, render, asset_key):
    import db
    tg_id = cb.from_user.id
    await cb.answer()
    if not await allowed(tg_id, bot, render):
        return
    saved = await state.get_data()
    period = saved.get('market_period', 60)
    session = secrets.token_hex(12)
    await state.set_state(Market.browsing.state)
    await state.update_data(market_session=session, market_period=period)
    token = progress = None
    consumed = delivery_attempted = False

    async def active():
        return (await state.get_state() == Market.browsing.state and
                (await state.get_data()).get('market_session') == session)

    async def retry():
        if not await active():
            raise api.AnalysisError('cancelled')
        snapshot.require_fresh()
        blocked = await api.reserve(db.pool, tg_id, retry=True)
        if blocked:
            raise api.AnalysisError(blocked)
        if progress:
            progress.retrying = True

    try:
        # Negative IDs separate callback analyses from positive upload message
        # IDs. The same Telegram message can only spend one analysis slot.
        blocked, token = await trial.reserve(db.pool, tg_id, -cb.message.message_id)
        if blocked:
            if blocked in ('trial', 'limit'):
                await allowed(tg_id, bot, render)
            else:
                await render(bot, tg_id, None, chart_signals.MESSAGES['busy'], RETRY)
            return
        if not api.configured():
            raise api.AnalysisError('configuration')
        await render(bot, tg_id, None, '⏳ Loading completed candles…', BACK, screen_key='market_loading')
        snapshot = await data.feed().candles(asset_key, period)
        if not await active():
            return
        png = await asyncio.to_thread(market_chart.render, snapshot)
        if not await active():
            return
        blocked = await api.reserve(db.pool, tg_id)
        if blocked:
            raise api.AnalysisError(blocked)
        language = await localization.language_for(tg_id)
        loading = await render(bot, tg_id, None, chart_progress.frame(language), BACK, screen_key='market_processing')
        progress = chart_progress.Progress(bot, tg_id, loading, language, active)
        progress.start()
        try:
            context = (f'Asset: {snapshot.asset.symbol}. Candle timeframe: {period} seconds. '
                       f'All candles shown are completed OHLC bars, sourced from OTCharts. '
                       f'Latest close: {snapshot.candles[-1].close:.8g}; UTC close time: {snapshot.closed_at}.')
            result = await api.analyse(png, language, context, before_retry=retry)
        finally:
            await progress.stop()
        if not await active():
            return
        snapshot.require_fresh()
        caption = result_text(snapshot, result)
        if not await trial.consume(db.pool, token):
            raise api.AnalysisError('limit')
        consumed = True
        if not await active():
            return
        rows = [[('🔄 New Analysis', 'cb:market:a:' + asset_key, 'success')],
                [('📊 Change Asset / Timeframe', 'cb:market:open', 'primary')],
                [('📸 Upload a Screenshot', 'cb:chart:upload')], *BACK]
        try:
            delivery_attempted = True
            await render(bot, tg_id, BufferedInputFile(png, filename='pocket-iq-chart.png'),
                         caption, rows, raise_on_error=True, screen_key='market_result')
        except (TelegramBadRequest, TelegramForbiddenError):
            await trial.release(db.pool, token, delivery_rejected=True)
            raise
    except (data.FeedError, api.AnalysisError) as exc:
        logging.warning('Asset analysis failed: code=%s', str(exc))
        if await active():
            messages = MESSAGES if isinstance(exc, data.FeedError) else chart_signals.MESSAGES
            await render(bot, tg_id, None, messages.get(str(exc), MESSAGES['service']), RETRY, screen_key='market_error')
    except Exception as exc:
        logging.warning('Asset request failed: type=%s', type(exc).__name__)
        if await active():
            await render(bot, tg_id, None, MESSAGES['service'], RETRY, screen_key='market_error')
    finally:
        if progress:
            await progress.stop()
        await trial.release(db.pool, token, delivery_rejected=consumed and not delivery_attempted)


def install(dp, render):
    async def command(m, bot, state):
        if m.chat.type != 'private':
            return
        await open_picker(bot, m.from_user.id, state, render)

    async def callback(cb, bot, state):
        if cb.message.chat.type != 'private':
            await cb.answer('Open the bot in a private chat.', show_alert=True)
            return
        action = cb.data.split(':')
        if len(action) == 3 and action[1] == 'a':
            return await analyse(cb, bot, state, render, action[2])
        if action == ['market', 'open']:
            return await open_screen(cb, bot, state, render)
        await cb.answer()
        if not await allowed(cb.from_user.id, bot, render, check_allowance=False):
            return
        await state.set_state(Market.browsing.state)
        await state.update_data(market_session=secrets.token_hex(12))
        page = 0
        if len(action) == 3 and action[1] == 'tf' and action[2] in ('60', '300'):
            await state.update_data(market_period=int(action[2]))
        elif len(action) == 3 and action[1] == 'p' and action[2].isdigit() and len(action[2]) < 5:
            page = int(action[2])
        elif action == ['market', 'all']:
            await state.update_data(market_query='')
        else:
            return
        try:
            await show_picker(bot, cb.from_user.id, state, render, page)
        except data.FeedError as exc:
            await render(bot, cb.from_user.id, None, MESSAGES.get(str(exc), MESSAGES['service']), RETRY)

    async def search(m, bot, state):
        if m.chat.type != 'private' or not await allowed(m.from_user.id, bot, render, check_allowance=False):
            return
        if not m.text or m.text.startswith('/'):
            await m.answer('Type an asset name, or use the buttons below.')
            return
        await state.update_data(market_query=m.text.strip()[:60], market_session=secrets.token_hex(12))
        try:
            await show_picker(bot, m.from_user.id, state, render)
        except data.FeedError as exc:
            await render(bot, m.from_user.id, None, MESSAGES.get(str(exc), MESSAGES['service']), RETRY)

    dp.message.register(command, Command('markets'))
    dp.callback_query.register(callback, F.data.startswith('market:'))
    dp.message.register(search, Market.browsing)
