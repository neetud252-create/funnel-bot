"""Offline activation: actual Start/Business routing, no Telegram or broker calls."""
import asyncio
from datetime import datetime, timezone, timedelta
import os
import types
from urllib.parse import urlparse, parse_qs
from unittest.mock import AsyncMock, patch
import test_signal_flow as H


async def main():
    db = H._install_stub_modules()
    B = H._load_bot()
    A, E = B.activation, B.elena
    os.environ['ELENA_ACTIVATION_ENABLED'] = '1'
    os.environ['ELENA_ENABLED'] = '1'
    now = datetime.now(timezone.utc)
    uid = 74001
    state = H.FakeState()
    bot = H.FakeBot()
    bot.id = 8790198817
    connection = dict(connection_id='approved', owner_id=99001, enabled=True, can_reply=True)
    inactive = dict(tg_id=uid, last_business_at=None, activated_at=None,
                    activation_opened_at=None, stopped=False, handoff=False)
    legacy = dict(inactive, activated_at=now-timedelta(days=1),
                  last_business_at=now, activation_opened_at=now, activation_version=0)
    accepted = dict(inactive, activated_at=now, last_business_at=now,
                    activation_version=E.store.ACTIVATION_VERSION)

    # /start, verified users and all resume deep links get exactly one URL button
    # until contact is proven. URL clicks cannot update activation or verification.
    for verified in (False, True):
        db._users[uid] = dict(db._fresh_row(), verified=verified, signals_used_today=9,
                             last_reset_date=db._today[0], uid='123456789' if verified else None)
        for resume in (None, 'elena_home', 'elena_setup', 'elena_uid', 'elena_signals', 'elena_guide'):
            with patch.object(E.store, 'session', AsyncMock(return_value=legacy)), \
                 patch.object(E.store, 'activate', AsyncMock()) as activate:
                await B.start(H.FakeMessage(uid, '/start'), bot, state,
                              types.SimpleNamespace(args=resume))
            card = bot.calls[-1]
            assert card['body'] == E.copy.ACTIVATION
            buttons = [b for row in card['markup'].inline_keyboard for b in row]
            assert len(buttons) == 1 and buttons[0].text == E.copy.ACTIVATE_BUTTON
            assert buttons[0].callback_data is None
            url = urlparse(buttons[0].url)
            assert url.scheme == 'tg' and url.netloc == 'resolve' and not url.path
            assert parse_qs(url.query) == {'domain':['elitecsupport'], 'text':[A.DRAFT]}
            activate.assert_not_awaited()
            assert db._users[uid]['verified'] == verified
            assert db._users[uid]['signals_used_today'] == 9
        assert not bot.pins

    assert E.keyboard(invitation=True).inline_keyboard[0][0].url == 'tg://resolve?domain=elitecsupport'

    # Stale feature buttons and typed IDs cannot bypass the contact gate.
    handler, gate = AsyncMock(), AsyncMock()
    cb = H.FakeCB(uid, 'menu:signal', 10)
    cb.from_user.is_bot = False
    cb.message.chat = types.SimpleNamespace(id=uid, type='private')
    message = H.FakeMessage(uid, '123456789')
    message.from_user.is_bot = False
    with patch.object(E.store, 'session', AsyncMock(return_value=inactive)), \
         patch.object(A, '_show_gate', gate):
        for event in (cb, message):
            state.state = 'Reg:waiting_uid'
            await A.GateMiddleware()(handler, event, dict(bot=bot, state=state))
            assert state.state is None
        handler.assert_not_awaited()
        assert gate.await_count == 2 and cb.answers == [(None, False)]
        # Opt-outs work even before activation; /start retains attribution routing.
        for text in ('/unsubscribe', '/subscribe', '/start campaign'):
            message.text = text
            await A.GateMiddleware()(handler, message, dict(bot=bot, state=state))
        assert handler.await_count == 3
        cb.data = 'vr:stop'
        await A.GateMiddleware()(handler, cb, dict(bot=bot, state=state))
        assert handler.await_count == 4
        # Business callbacks remain inside the authorized business handler.
        cb.data = 'elena:consent'
        cb.message.business_connection_id = 'approved'
        await A.GateMiddleware()(handler, cb, dict(bot=bot, state=state))
        assert handler.await_count == 5

    # A failed image upload leaves the same one-button text screen.
    with patch.object(E.store, 'session', AsyncMock(return_value=inactive)), \
         patch.object(B, 'send_media', AsyncMock(side_effect=RuntimeError('image unavailable'))):
        await B._show_activation_gate(bot, uid)
        assert bot.calls[-1]['kind'] == 'text' and bot.calls[-1]['body'] == E.copy.ACTIVATION

    # Authentic incoming DM activates once and sends the CURRENT interface in
    # the normal bot automatically, before any AI-consent prompt/model request.
    for verified in (False, True):
        db._users[uid] = dict(db._fresh_row(), verified=verified, signals_used_today=9,
                             last_reset_date=db._today[0], uid='123456789' if verified else None)
        bot = H.FakeBot()
        bot.id = 8790198817
        m = types.SimpleNamespace(chat=types.SimpleNamespace(id=uid, type='private'),
            from_user=types.SimpleNamespace(id=uid), message_id=11, text=A.DRAFT,
            business_connection_id='approved', sender_business_bot=None, via_bot=None, is_from_offline=False)
        # The session returned to the opener already contains the durable activation.
        with patch.object(E.store, 'connection', AsyncMock(return_value=connection)), \
             patch.object(E.store, 'session', AsyncMock(return_value=accepted)), \
             patch.object(E.store, 'incoming', AsyncMock(return_value=legacy)) as incoming, \
             patch.object(E.store, 'activate', AsyncMock(return_value=accepted)) as activate, \
             patch.object(E.store, 'claim_activation', AsyncMock(return_value={'tg_id':uid})), \
             patch.object(E.store, 'activation_opened', AsyncMock()) as opened, \
             patch.object(E, 'send_business', AsyncMock()) as send, \
             patch.object(E.ai, 'reply', AsyncMock()) as ai_reply:
            if verified:
                send.side_effect = RuntimeError('Elena confirmation unavailable')
            await E.business_message(m, bot)
            activate.assert_awaited_once_with(uid, 'approved', 11,
                'ready' if verified else 'inactive', 1800)
            assert send.await_args.args[2] == E.copy.ACTIVATED
            assert send.await_args.args[3].inline_keyboard[0][0].url == E.link()
            opened.assert_awaited_once_with(uid)
            ai_reply.assert_not_awaited()
            assert bot.calls[-1]['asset'] == ('assets/menu.png' if verified else 'assets/home_banner_loop.mp4')
            assert db._users[uid]['verified'] == verified
            assert db._users[uid]['signals_used_today'] == 9
            count = len(bot.calls)
            incoming.return_value = None
            await E.business_message(m, bot)
            assert len(bot.calls) == count and send.await_count == 1

        # No synthetic activation on foreign connection, forwarded bot echoes,
        # mismatched chat, unrelated contact, or a disabled connection.
        with patch.object(E.store, 'connection', AsyncMock(return_value=connection)), \
             patch.object(E.store, 'incoming', AsyncMock()) as incoming, \
             patch.object(E.store, 'activate', AsyncMock()) as activate:
            for changes in ({'business_connection_id':'foreign'}, {'sender_business_bot':object()},
                            {'from_user':types.SimpleNamespace(id=uid+1)}):
                await E.business_message(types.SimpleNamespace(**(m.__dict__ | changes)), bot)
            with patch.object(E.db, 'get_user', AsyncMock(return_value=None)):
                await E.business_message(m, bot)
            with patch.object(E.store, 'connection', AsyncMock(return_value=connection | {'enabled':False})):
                await E.business_message(m, bot)
            incoming.assert_not_awaited()
            activate.assert_not_awaited()

    # Only NEW activation survives the business window and deployments. All old
    # contacts/activated accounts, including admins, must activate this release.
    for saved in (accepted, dict(accepted, last_business_at=now-timedelta(days=10))):
        with patch.object(E.store, 'session', AsyncMock(return_value=saved)):
            assert not await A.required(uid)
    for saved in (inactive, legacy, dict(inactive, last_business_at=now-timedelta(days=10)),
                  dict(accepted, activation_version=0)):
        with patch.object(E.store, 'session', AsyncMock(return_value=saved)):
            assert await A.required(uid)
            with patch.object(B.config, 'ADMIN_IDS', [uid]):
                assert await A.required(uid), 'Admin /start previews the same activation screen'

    # Old queued handoffs cannot automatically reopen access after the reset.
    with patch.object(E.store, 'session', AsyncMock(return_value=legacy)), \
         patch.object(B, '_show_current_interface', AsyncMock()) as show:
        assert not await B._open_from_elena(bot, uid)
        show.assert_not_awaited()
    with patch.object(E.store, 'await_chat', AsyncMock()) as defer, \
         patch.object(E, 'active_connection', AsyncMock()) as connection_lookup:
        await E.send_job(bot, object(), legacy)
        defer.assert_awaited_once()
        connection_lookup.assert_not_awaited()
    cb.answers.clear()
    with patch.object(E, 'active_connection', AsyncMock(return_value=connection)), \
         patch.object(E.store, 'session', AsyncMock(return_value=legacy)), \
         patch.object(E.localization, 'language_for', AsyncMock(return_value='en')), \
         patch.object(E.store, 'consent', AsyncMock()) as consent:
        await E.callback(cb, bot)
        assert cb.answers == [(E.copy.REOPEN, True)]
        consent.assert_not_awaited()

    # Slow Elena delivery cannot delay the menu being triggered in Pocket IQ.
    confirmation_started, release_confirmation, menu_opened = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def slow_confirmation(*args):
        confirmation_started.set()
        await release_confirmation.wait()
    async def instant_menu(*args):
        menu_opened.set()
    with patch.object(E.store, 'connection', AsyncMock(return_value=connection)), \
         patch.object(E.store, 'session', AsyncMock(return_value=legacy)), \
         patch.object(E.store, 'incoming', AsyncMock(return_value=legacy)), \
         patch.object(E.store, 'activate', AsyncMock(return_value=accepted)), \
         patch.object(E, 'confirm_activation', slow_confirmation), \
         patch.object(A, 'deliver', instant_menu):
        task = asyncio.create_task(E.business_message(m, bot))
        try:
            await asyncio.wait_for(confirmation_started.wait(), 1)
            await asyncio.wait_for(menu_opened.wait(), 1)
            assert not task.done()
        finally:
            release_confirmation.set()
            await task

    # A new activation confirmation is transactional; it preserves earlier
    # opt-out/handoff preferences instead of silently resuming follow-ups.
    with patch.object(E, 'send_business', AsyncMock()) as send:
        await E.confirm_activation(bot, uid, {'language':'en'})
        assert 'activated successfully' in send.await_args.args[2]

    # Durable claim controls retries. Failures never pretend the menu was sent.
    with patch.object(E.store, 'claim_activation', AsyncMock(return_value={'tg_id':uid})) as claim, \
         patch.object(E.store, 'activation_opened', AsyncMock()) as opened, \
         patch.object(A, '_open_interface', AsyncMock(side_effect=RuntimeError('offline'))) as open_menu:
        await A.deliver(bot, uid)
        opened.assert_not_awaited()
        claim.return_value = None
        await A.deliver(bot, uid)
        assert open_menu.await_count == 1

    # Localized one-button gate, unchanged usernames/URL, caption size safety.
    import localization as L
    import re
    for language in L.CODES[1:]:
        for source in (E.copy.ACTIVATION, E.copy.ACTIVATED, E.copy.ACTIVATE_BUTTON):
            translated = L.translate_parts(source, language)
            assert translated != source
            assert len(re.sub('<[^>]+>', '', translated).encode('utf-16-le')) // 2 <= 1024
        assert '@goplusaitrader_bot' in L.translate_parts(E.copy.ACTIVATED, language)
    print('PASS: single-button gate, Start/resume protection, stale callbacks, authentic DM activation, automatic current menu, duplicate suppression, entitlement preservation, delivery failures and all languages.')


if __name__ == '__main__':
    asyncio.run(main())
