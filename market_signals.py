"""Asset picker -> validated candles -> generated chart -> existing AI analysis."""
import asyncio
import html
import logging
import secrets

from aiogram import F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

import chart_analysis as api
import market_progress
import chart_signals
import chart_strategy
import chart_trial as trial
import localization
import market_chart
import market_data as data
import market_catalog as catalog

PAGE_SIZE = 6
EXPIRIES = tuple(range(1, 11))
SECOND_EXPIRIES = (5, 10, 15, 20, 25, 30, 45, 50, 55)
DIRECTION_BANNERS = {'BUY': 'signal_buy', 'SELL': 'signal_sell'}
BACK = [[('⬅️ Home', 'cb:home:back', 'danger')]]
RETRY = [[('📊 Choose Asset', 'cb:market:open', 'primary')],
         *BACK]
ACCESS = [[('🚀 Get Full Access', 'cb:home:access', 'success')],
          [('📊 Browse Assets', 'cb:market:open', 'primary')],
          [('💬 Support', 'cb:flow:support')], *BACK]
MESSAGES = {
    'configuration': 'Live asset analysis is being connected. You can browse all markets and assets now.',
    'plan': 'Live candles for this asset are not available from the connected feed. Please choose another asset.',
    'quota': 'The market-data allowance is currently exhausted. Please try again later.',
    'asset': 'This asset is no longer available. Please choose another asset.',
    'empty': 'No assets are available from the connected feed right now.',
    'closed': 'This asset is currently closed for trading. Please choose another asset.',
    'stale': 'Fresh candles are not available for this asset yet. Please try again shortly.',
    'gaps': 'The candle history has missing data. Please try another asset or try again later.',
    'history': 'There are not enough completed candles to analyse this asset yet.',
    'invalid': 'The candle data could not be verified. No analysis was used. Please try again later.',
    'service': 'Market data is temporarily unavailable. Please try again shortly.',
}
MARKET_TITLE = '📊 <b>Select market type</b>'
CATEGORY_TITLE = '📋 <b>Select an asset by category</b>\n{market}'
PICKER_TITLE = '💱 <b>{title}</b>\n{market} · {count} assets'
EMPTY_CATEGORY = '📋 <b>{title}</b>\n\nNo assets are listed in this market category yet.'
EXPIRATION_TITLE = '⏱ <b>Choose the expiration time</b>\n{asset}'
SEARCH = '\n\nSearch: <b>{query}</b>'
NO_MATCHES = '\nNo matching assets. Try another name.'
RESULT = ('✅ <b>Your Signal Is Ready!</b>\n\n'
          '{asset_label} <b>{asset}</b>\n'
          '⏱ Expiration: <b>{expiry}</b>\n'
          '<b>Reason:</b> {reason}{qualification}\n\n'
          '{direction_icon} <b>{direction}</b>')
ALERTS = ('Open the bot in a private chat.', 'Choose an expiration using the buttons below.',
          'Type an asset name, or use the buttons below.',
          'Choose a market and category using the buttons below.')
SOURCES = (*MESSAGES.values(), MARKET_TITLE, CATEGORY_TITLE, PICKER_TITLE,
           EMPTY_CATEGORY, EXPIRATION_TITLE, SEARCH, NO_MATCHES, RESULT, 'Asset:', *ALERTS)


class Market(StatesGroup):
    browsing = State()


def filtered(assets, query):
    query = catalog.normalized(query)
    return [a for a in assets if query in catalog.normalized(a.name)]


def picker(assets, market='otc', category='forex', page=0, query='', language='en'):
    matches = filtered(assets, query)
    pages = max(1, (len(matches) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages-1))
    tr = lambda text: localization.translate_parts(text, language)
    title = tr(catalog.category_info(category)[1])
    text = tr(PICKER_TITLE).format(title=title, market=market.upper(), count=len(matches))
    if query:
        text += tr(SEARCH).format(query=html.escape(query))
    if not matches:
        text += tr(NO_MATCHES)
    buttons = [(a.name[:50], 'cb:market:a:' + a.key, 'primary')
               for a in matches[page*PAGE_SIZE:(page+1)*PAGE_SIZE]]
    rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    nav = []
    prefix = f'cb:market:list:{market}:{category}:'
    if page:
        nav.append(('‹', prefix + str(page-1), 'primary'))
    nav.append((f'{page+1}/{pages}', 'cb:market:noop', 'primary'))
    if page+1 < pages:
        nav.append(('›', prefix + str(page+1), 'primary'))
    rows.append(nav)
    if query:
        rows.append([('Clear Search', 'cb:market:all', 'primary')])
    rows += [[('« Back', 'cb:market:book:' + market, 'danger')]]
    return text, rows


async def allowed(tg_id, bot, render, *, check_allowance=True):
    import db
    # The public catalogue is independent of the feed plan and trial quota.
    if not check_allowance:
        return True
    if not data.enabled(tg_id):
        await render(bot, tg_id, None, MESSAGES['configuration'], RETRY, screen_key='market_unavailable')
        return False
    verified, left = await trial.status(db.pool, tg_id)
    if not left:
        text = chart_signals.MESSAGES['limit'] if verified else chart_signals.EXHAUSTED
        await render(bot, tg_id, None, text, ACCESS, screen_key='market_limit')
        return False
    return True


async def available_catalog(tg_id, *, strict=False):
    if data.enabled(tg_id):
        try:
            return catalog.entries(await data.feed().assets())
        except data.FeedError:
            if strict:
                raise
            pass  # The public catalogue remains usable during a feed outage.
    return catalog.entries()


async def active(state, session):
    return (await state.get_state() == Market.browsing.state and
            (await state.get_data()).get('market_session') == session)


async def navigate(state, **values):
    await state.set_state(Market.browsing.state)
    session = secrets.token_hex(12)
    await state.update_data(market_session=session, **values)
    return session


async def show_categories(bot, tg_id, state, render, market):
    session = await navigate(state, market_book=market, market_category=None, market_query='')
    items = await available_catalog(tg_id)
    if not await active(state, session):
        return
    categories = list(catalog.CATEGORIES)
    if any(e.market == market and e.category == 'other' for e in items):
        categories.append('other')
    rows = [[(catalog.category_info(c)[0], f'cb:market:cat:{market}:{c}', 'primary')]
            for c in categories]
    rows.append([('« Back', 'cb:market:open', 'danger')])
    await render(bot, tg_id, 'asset_category', CATEGORY_TITLE.format(market=market.upper()),
                 rows, screen_key='market_categories')


async def show_picker(bot, tg_id, state, render, page=0):
    saved = await state.get_data()
    session = saved.get('market_session')
    market, category = saved.get('market_book', 'otc'), saved.get('market_category') or 'forex'
    assets = [e for e in await available_catalog(tg_id) if e.market == market and e.category == category]
    if not await active(state, session):
        return
    pages = max(1, (len(filtered(assets, saved.get('market_query', ''))) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(0, page), pages - 1)
    await state.update_data(market_page=page)
    language = await localization.language_for(tg_id)
    text, rows = picker(assets, market, category, page, saved.get('market_query', ''), language)
    if not assets:
        text = localization.translate_parts(EMPTY_CATEGORY, language).format(
            title=localization.translate_parts(catalog.category_info(category)[0], language, button=True))
    await render(bot, tg_id, catalog.category_info(category)[2], text, rows, screen_key='market_assets')


async def select_asset(bot, tg_id, state, render, key):
    session = await navigate(state)
    try:
        entry = catalog.find(await available_catalog(tg_id), key)
    except data.FeedError as exc:
        if await active(state, session):
            await render(bot, tg_id, None, MESSAGES.get(str(exc), MESSAGES['service']), RETRY)
        return
    if not await active(state, session):
        return
    if entry is None:
        return await render(bot, tg_id, None, MESSAGES['asset'], RETRY)
    saved = await state.get_data()
    page = saved.get('market_page', 0) if (saved.get('market_book'), saved.get('market_category')) == (entry.market, entry.category) else 0
    await state.update_data(market_book=entry.market, market_category=entry.category, market_asset=entry.key)
    back = [[('« Back', f'cb:market:list:{entry.market}:{entry.category}:{page}', 'danger')]]
    language = await localization.language_for(tg_id)
    tr = lambda text: localization.translate_parts(text, language)
    text = tr(EXPIRATION_TITLE).format(asset=html.escape(entry.name))
    if not data.enabled(tg_id):
        text += '\n\n' + tr(MESSAGES['configuration'])
    elif not entry.live:
        text += '\n\n' + tr(MESSAGES['plan'])
    elif entry.live.open is False:
        text += '\n\n' + tr(MESSAGES['closed'])
    await render(bot, tg_id, 'expiration_time', text, [*expiration_rows(entry.key), *back],
                 screen_key='market_expiration')


def expiration_rows(key):
    seconds = lambda n: (f'S{n}', expiry_callback(key, n, 's'), 'primary')
    minute = lambda n: ((('🔥 ' if n == 1 else '✅ ' if n == 10 else '') + f'M{n}'),
                        f'cb:market:expire:{key}:{n}', 'primary')
    return [[seconds(n) for n in group] for group in ((5,), (10, 15), (20, 25, 30), (45, 50, 55))] + [
        [minute(n) for n in group] for group in ((1,), (2, 3), (4, 5, 6), (7, 8, 9), (10,))]


def valid_expiry(expiry, unit):
    return type(expiry) is int and (
        (unit == 's' and expiry in SECOND_EXPIRIES) or
        (unit == 'm' and expiry in EXPIRIES))


def expiry_callback(key, expiry, unit):
    # Keep existing minute callbacks working; seconds carry their own unit.
    action = 'expire_s' if unit == 's' else 'expire'
    return f'cb:market:{action}:{key}:{expiry}'


async def open_screen(cb, bot, state, render):
    if cb.message.chat.type != 'private':
        await cb.answer('Open the bot in a private chat.', show_alert=True)
        return
    await cb.answer()
    await open_picker(bot, cb.from_user.id, state, render)


async def open_picker(bot, tg_id, state, render):
    await state.clear()
    await navigate(state, market_period=60, market_query='')
    await render(bot, tg_id, 'trading_type', MARKET_TITLE,
                 [[('OTC', 'cb:market:book:otc', 'primary')],
                  [('FIN', 'cb:market:book:fin', 'primary')],
                  [('« Back', 'cb:home:back', 'danger')]], screen_key='market_type')


def result_text(snapshot, result, expiry=1, language='en', *, expiry_unit='m'):
    # The strategy's validated direction is retained even for a tentative bias.
    # Invalid/unreadable input still fails; never manufacture a direction.
    confirmed = chart_strategy.confirmed_setup(result)
    direction = result.get('direction')
    if not chart_strategy.supports_signal(result) or not valid_expiry(expiry, expiry_unit):
        raise api.AnalysisError('response')
    tr = lambda text: localization.translate_parts(text, language)
    qualification = '' if confirmed else '\n<i>' + tr(chart_strategy.TENTATIVE_NOTE) + '</i>'
    reason = result.get('reason')
    if not isinstance(reason, str) or not reason.strip():
        raise api.AnalysisError('response')
    reason = ' '.join(reason.split())
    if len(reason) > 240:
        reason = reason[:237].rsplit(' ', 1)[0].rstrip('.,;:') + '…'
    entry = catalog.find(catalog.entries((snapshot.asset,)), snapshot.asset.key)
    is_forex = entry and entry.category == 'forex'
    label = ('💱 ' + tr('Currency pair:')) if is_forex else ('📊 ' + tr('Asset:'))
    return tr(RESULT).format(asset_label=label, asset=html.escape(snapshot.asset.name),
        expiry=f'{expiry_unit.upper()}{expiry}', reason=html.escape(reason),
        qualification=qualification, direction_icon='📈' if direction == 'BUY' else '📉', direction=direction)


async def analyse(cb, bot, state, render, asset_key, *, period=None, expiry=1, expiry_unit='m'):
    tg_id = cb.from_user.id
    # One in-flight analysis per user, including callbacks on older messages.
    if tg_id in chart_signals._inflight or len(chart_signals._inflight) >= api.MAX_CONCURRENT:
        await cb.answer(chart_signals.MESSAGES['busy'], show_alert=True)
        return
    # Claim before the first await, including the callback acknowledgement.
    # Otherwise two fast taps can both pass the check and replace each other's
    # session while reserving two allowances.
    chart_signals._inflight.add(tg_id)
    try:
        await _analyse(cb, bot, state, render, asset_key, period=period, expiry=expiry, expiry_unit=expiry_unit)
    finally:
        chart_signals._inflight.discard(tg_id)


async def _analyse(cb, bot, state, render, asset_key, *, period=None, expiry=1, expiry_unit='m'):
    import db
    tg_id = cb.from_user.id
    if not valid_expiry(expiry, expiry_unit):
        await cb.answer('Choose an expiration using the buttons below.', show_alert=True)
        return
    await cb.answer()
    if not await allowed(tg_id, bot, render):
        return
    # Expiry is user-selected, not a candle API period. All new choices use
    # completed M1 bars; only old 1/5-minute chart callbacks retain their period.
    period = period if period in data.PERIODS else 60
    session = secrets.token_hex(12)
    await state.set_state(Market.browsing.state)
    await state.update_data(market_session=session, market_period=period,
                            market_expiry=expiry, market_expiry_unit=expiry_unit)
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
            await progress.reconnecting()

    try:
        entry = catalog.find(catalog.entries(await data.feed().assets()), asset_key)
        if not await active():
            return
        if not entry or not entry.live:
            raise data.FeedError('plan')
        if entry.live.open is False:
            raise data.FeedError('closed')
        # The same Telegram menu message can only spend one analysis slot.
        # Negative IDs also keep historic upload usage records distinct.
        blocked, token = await trial.reserve(db.pool, tg_id, -cb.message.message_id)
        if blocked:
            if blocked in ('trial', 'limit'):
                await allowed(tg_id, bot, render)
            else:
                await render(bot, tg_id, None, chart_signals.MESSAGES['busy'], RETRY)
            return
        if not api.configured():
            raise api.AnalysisError('configuration')
        language = await localization.language_for(tg_id)
        progress = market_progress.Loading(bot, tg_id, language, active)
        await progress.start(render)
        if not await active():
            return
        snapshot = await data.feed().candles(entry.live.key, period)
        if not await active():
            return
        png = await asyncio.to_thread(market_chart.render, snapshot)
        if not await active():
            return
        blocked = await api.reserve(db.pool, tg_id)
        if blocked:
            raise api.AnalysisError(blocked)
        try:
            context = (f'Asset: {snapshot.asset.symbol}. Candle timeframe: {period} seconds. '
                       f'User-selected expiration: {expiry} {"seconds" if expiry_unit == "s" else "minutes"}. This is a requested horizon, '
                       f'not proof that an entry is confirmed or an optimal expiry. '
                       f'All candles shown are completed OHLC bars, sourced from {snapshot.source}. '
                       f'Latest close: {snapshot.candles[-1].close:.8g}; UTC close time: {snapshot.closed_at}.')
            result = await api.analyse(png, language, context, before_retry=retry)
        finally:
            await progress.stop()
        if not await active():
            return
        snapshot.require_fresh()
        caption = result_text(snapshot, result, expiry, language, expiry_unit=expiry_unit)
        if not await trial.consume(db.pool, token):
            if not await allowed(tg_id, bot, render):
                return
            raise api.AnalysisError('service')
        consumed = True
        verified, _ = await trial.status(db.pool, tg_id)
        if not await active():
            return
        rows = [[('🔄 New Analysis', expiry_callback(entry.key, expiry, expiry_unit), 'success')],
                [('⏱ Change Expiration', f'cb:market:a:{entry.key}', 'primary')],
                [('📊 Change Asset', 'cb:market:open', 'primary')],
                *BACK]
        if not verified:
            caption += '\n\n' + localization.translate_parts(chart_signals.EXHAUSTED, language)
            rows = ACCESS
        try:
            delivery_attempted = True
            await render(bot, tg_id, BufferedInputFile(png, filename='pocket-iq-chart.png'),
                         caption, rows, raise_on_error=True, screen_key='market_result',
                         companion_key=DIRECTION_BANNERS[result['direction']])
        except (TelegramBadRequest, TelegramForbiddenError):
            await trial.release(db.pool, token, delivery_rejected=True)
            raise
    except (data.FeedError, api.AnalysisError) as exc:
        logging.warning('Asset analysis failed: code=%s', str(exc))
        if await active():
            messages = MESSAGES if isinstance(exc, data.FeedError) else {
                **chart_signals.MESSAGES, 'chart': MESSAGES['history'], 'image': MESSAGES['invalid']}
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
        if len(action) == 4 and action[1] in ('expire', 'expire_s'):
            unit = 's' if action[1] == 'expire_s' else 'm'
            choices = SECOND_EXPIRIES if unit == 's' else EXPIRIES
            if action[3] not in tuple(map(str, choices)):
                await cb.answer('Choose an expiration using the buttons below.', show_alert=True)
                return
            return await analyse(cb, bot, state, render, action[2], expiry=int(action[3]), expiry_unit=unit)
        if len(action) == 3 and action[1] == 'locked':
            # Historic locked buttons contain no asset key. Reopen the current
            # asset's unlocked picker instead of guessing which asset they meant.
            await cb.answer()
            saved = await state.get_data()
            if saved.get('market_asset'):
                return await select_asset(bot, cb.from_user.id, state, render, saved['market_asset'])
            return await open_picker(bot, cb.from_user.id, state, render)
        if len(action) == 4 and action[1] == 'run' and action[3] in ('60', '300'):
            # Period travels with the callback, so an old button cannot silently
            # analyse a newly selected timeframe. analyse claims concurrency
            # before its first await.
            return await analyse(cb, bot, state, render, action[2], period=int(action[3]),
                                 expiry=int(action[3])//60)
        if action == ['market', 'open']:
            return await open_screen(cb, bot, state, render)
        await cb.answer()
        if action == ['market', 'noop']:
            return
        tg_id = cb.from_user.id
        if len(action) == 3 and action[1] == 'a':
            return await select_asset(bot, tg_id, state, render, action[2])
        if len(action) == 3 and action[1] == 'book' and action[2] in ('otc', 'fin'):
            return await show_categories(bot, tg_id, state, render, action[2])
        if (len(action) in (4, 5) and action[1] in ('cat', 'list') and action[2] in ('otc', 'fin')
                and action[3] in (*catalog.CATEGORIES, 'other')):
            if len(action) == 5 and (not action[4].isdigit() or len(action[4]) > 4):
                return
            saved = await state.get_data()
            same = (saved.get('market_book'), saved.get('market_category')) == (action[2], action[3])
            query = saved.get('market_query', '') if same and action[1] == 'list' else ''
            await navigate(state, market_book=action[2], market_category=action[3], market_query=query)
            return await show_picker(bot, tg_id, state, render, int(action[4]) if len(action) == 5 else 0)
        elif action == ['market', 'all']:
            await navigate(state, market_query='')
            return await show_picker(bot, tg_id, state, render)
        else:
            # Old flat-picker / timeframe callbacks migrate to the new menu.
            return await open_picker(bot, tg_id, state, render)

    async def search(m, bot, state):
        if m.chat.type != 'private':
            return
        if not m.text or m.text.startswith('/'):
            await m.answer('Type an asset name, or use the buttons below.')
            return
        saved = await state.get_data()
        if not saved.get('market_category'):
            return await m.answer('Choose a market and category using the buttons below.')
        await navigate(state, market_query=m.text.strip()[:60])
        await show_picker(bot, m.from_user.id, state, render)

    async def retired_upload(m, bot, state):
        if m.chat.type == 'private':
            await open_picker(bot, m.from_user.id, state, render)

    async def legacy(cb, bot, state):
        await open_screen(cb, bot, state, render)

    dp.message.register(command, Command('markets', 'assets', 'signals'))
    dp.callback_query.register(callback, F.data.startswith('market:'))
    # Install before legacy trading callbacks: no stale expiry or upload button
    # may enter the retired random-signal or screenshot implementations.
    dp.callback_query.register(legacy, F.data.regexp(
        r'^(chart:|mode:|type:|asset:|pair:|pairpage:|m:|s:|new_signal$|go:(mode|type|asset|pairs|test_menu|trading_mode)$)'))
    dp.message.register(retired_upload, F.photo | F.document)
    dp.message.register(search, Market.browsing, F.text, ~F.text.startswith('/'))
