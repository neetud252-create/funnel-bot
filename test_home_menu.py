"""Offline checks for the public welcome menu and protected access routing."""
import asyncio
import types
from unittest.mock import AsyncMock, patch
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendAnimation
import test_signal_flow as H


async def main():
    fake_db = H._install_stub_modules()
    bot_mod = H._load_bot()
    import home_menu
    import config

    rows = bot_mod.build_kb(home_menu.keyboard()).inline_keyboard
    assert [len(row) for row in rows] == [1, 2, 2, 2, 1]
    assert [button.text for row in rows for button in row] == [
        '💎 Get Bot Access', '📖 Guide', '🎯 Test Signals', '⭐ Review',
        '☎️ Support', '▶️ YouTube', '💡 Forex Tips', '🌐 Change Language']
    assert rows[0][0].callback_data == 'home:access'
    for value in ('@flashhher', 'https://t.me/flashhher'):
        with patch.object(config, 'SUPPORT', value):
            assert home_menu.keyboard()[2][1][1] == 'url:https://t.me/flashhher'
    with patch.object(config, 'FOREX_TIPS_URL', 'https://t.me/PLACEHOLDER_FOREX_TIPS'):
        assert home_menu.keyboard()[3][1][1] == 'cb:home:tips'

    for verified in (False, True):
        uid = 72000 + int(verified)
        fake_db._users[uid] = fake_db._fresh_row()
        fake_db._users[uid].update(verified=verified, uid='123456789',
                                   deposit=50, gift_sent_at='earlier')
        initial = dict(fake_db._users[uid])
        bot = H.FakeBot()
        state = H.FakeState()
        command = types.SimpleNamespace(args='tracking-code')
        with patch.object(bot_mod, '_capture_ref', AsyncMock()) as attribution:
            await bot_mod.start(H.FakeMessage(uid, '/start'), bot, state, command)
            attribution.assert_awaited_once_with(uid, command)
        sends = [c for c in bot.calls if c['kind'] != 'delete']
        assert len(sends) == 1 and sends[0]['kind'] == 'animation'
        assert sends[0]['asset'] == 'assets/home_banner_loop.mp4'
        assert sends[0]['body'] == home_menu.TEXT
        assert sends[0]['markup'] is not None
        assert {k:v for k,v in fake_db._users[uid].items() if k!='ui_msg_id'} == {
            k:v for k,v in initial.items() if k!='ui_msg_id'}
        assert state.state is None

        await bot_mod.home_action(H.FakeCB(uid, 'home:back', 1), bot, state)
        assert bot.calls[-1]['kind'] == 'animation'
        assert bot.calls[-1]['asset'] == 'assets/home_banner_loop.mp4'
        assert bot.calls[-1]['body'] == home_menu.TEXT

        with patch.object(bot_mod, '_show_menu', AsyncMock()) as menu, \
             patch.object(bot_mod, 'show', AsyncMock()) as show:
            await bot_mod.home_action(H.FakeCB(uid, 'home:access', 1), bot, state)
            if verified:
                menu.assert_awaited_once_with(bot, uid)
                show.assert_not_awaited()
            else:
                show.assert_awaited_once_with(bot, uid, 'register')
                assert state.state == bot_mod.Reg.waiting_uid.state
                bot_mod._nudge_tasks.pop(uid).cancel()
                menu.assert_not_awaited()

    # New users reach the requested photo immediately with tracked links in
    # both the caption and Register button; typing an ID is already enabled.
    uid = 72020
    fake_db._users[uid] = dict(fake_db._fresh_row(), ref_code='campaign_42')
    bot = H.FakeBot()
    state = H.FakeState()
    await bot_mod.home_action(H.FakeCB(uid, 'home:access', 1), bot, state)
    screen = bot.calls[-1]
    assert screen['kind'] == 'photo' and screen['asset'] == 'assets/register.jpg'
    ref = config.ref_url('campaign_42')
    assert ref in screen['body'] and screen['markup'].inline_keyboard[0][0].url == ref
    assert [row[0].callback_data for row in screen['markup'].inline_keyboard] == [
        None, 'reg:enter_uid', 'home:back']
    assert state.state == bot_mod.Reg.waiting_uid.state
    assert not fake_db._users[uid]['verified']

    await bot_mod.enter_registration_uid(H.FakeCB(uid, 'reg:enter_uid', 1), bot, state)
    assert bot.calls[-1]['body'] == config.MSG_ENTER_UID
    assert bot.calls[-1]['markup'].inline_keyboard[0][0].callback_data == 'go:register'
    assert state.state == bot_mod.Reg.waiting_uid.state and uid not in bot_mod._nudge_tasks
    with patch.object(bot_mod, '_verify_once', AsyncMock(return_value=config.VERIFY_GRANTED)) as verify:
        await bot_mod.capture_uid(H.FakeMessage(uid, 'not-an-ID'), bot, state)
        verify.assert_not_awaited()
        assert 'numbers only' in bot.calls[-1]['body']
        await bot_mod.capture_uid(H.FakeMessage(uid, '123456789'), bot, state)
        verify.assert_awaited_once_with(bot, uid, '123456789')
    # Stale ID buttons do not ask an already verified user to register again.
    with patch.object(bot_mod, '_show_menu', AsyncMock()) as menu:
        await bot_mod.enter_registration_uid(H.FakeCB(72001, 'reg:enter_uid', 1), bot, state)
        menu.assert_awaited_once_with(bot, 72001)
    await bot_mod.nav(H.FakeCB(uid, 'go:register', 1), bot, state)
    assert bot.calls[-1]['asset'] == 'assets/register.jpg'
    await bot_mod.home_action(H.FakeCB(uid, 'home:back', 1), bot, state)
    assert bot.calls[-1]['kind'] == 'animation' and state.state is None
    bot_mod._nudge_tasks.pop(uid).cancel()

    for action in ('test', 'tips'):
        cb = H.FakeCB(72000, 'home:' + action, 1)
        bot = H.FakeBot()
        state = H.FakeState()
        state.state = 'registration-in-progress'
        await bot_mod.home_action(cb, bot, state)
        assert not bot.calls
        assert state.state == 'registration-in-progress'
        assert cb.answers == [(home_menu.PENDING[action], True)]

    # Missing/unavailable animations keep a usable welcome and its buttons.
    bot = H.FakeBot()
    with patch.object(bot_mod, 'media_missing', side_effect=lambda key, ext: key == home_menu.ANIMATION):
        await bot_mod.start(H.FakeMessage(72010, '/start'), bot, H.FakeState())
    assert bot.calls[-1]['kind'] == 'photo'
    assert bot.calls[-1]['asset'] == 'assets/home_banner.png'
    assert bot.calls[-1]['body'] == home_menu.TEXT and bot.calls[-1]['markup']

    bot = H.FakeBot()
    bot.send_animation = AsyncMock(side_effect=TelegramBadRequest(
        method=SendAnimation(chat_id=72010, animation='bad-id'), message='Invalid animation'))
    await bot_mod.start(H.FakeMessage(72010, '/start'), bot, H.FakeState())
    assert bot.calls[-1]['kind'] == 'photo'
    assert bot.calls[-1]['body'] == home_menu.TEXT and bot.calls[-1]['markup']
    assert fake_db._users[72010]['ui_msg_id'] == bot.calls[-1]['id']

    # Telegram animation.file_id is reused and never mixed with the old photo.
    key = home_menu.ANIMATION
    result = types.SimpleNamespace(animation=types.SimpleNamespace(file_id='animation-file-id'))
    await bot_mod.remember_video(key, result)
    assert bot_mod.cached_id(key, 'mp4') == 'animation-file-id'
    assert fake_db._media_cache[key][0] == 'animation-file-id'
    assert key not in bot_mod._photo_cache
    bot = H.FakeBot()
    await bot_mod.start(H.FakeMessage(72011, '/start'), bot, H.FakeState())
    assert bot.calls[-1]['kind'] == 'animation'
    assert bot.calls[-1]['asset'] == 'animation-file-id'

    # An expired cached animation retries from the local MP4 once.
    rejection = TelegramBadRequest(
        method=SendAnimation(chat_id=72011, animation='animation-file-id'), message='Wrong file identifier')
    bot.send_animation = AsyncMock(side_effect=[rejection, result])
    await bot_mod.send_media(bot, 72011, key, True, home_menu.TEXT, None)
    calls = bot.send_animation.await_args_list
    assert len(calls) == 2 and calls[0].args[1] == 'animation-file-id'
    assert str(calls[1].args[1].path) == 'assets/home_banner_loop.mp4'
    assert key not in bot_mod._video_cache

    # Startup must upload the loop as an animation, so its cached ID is reusable.
    bot = H.FakeBot()
    result.message_id = 900
    bot.send_animation = AsyncMock(return_value=result)
    with patch.object(config, 'MEDIA_WARM_CHAT', 72011), \
         patch.object(bot_mod, 'referenced_assets', return_value={(key, 'mp4')}), \
         patch.object(bot_mod.asyncio, 'sleep', AsyncMock()):
        await bot_mod.warm_media_cache(bot)
    bot.send_animation.assert_awaited_once()
    assert bot_mod.cached_id(key, 'mp4') == 'animation-file-id'
    bot_mod._video_cache.clear()
    print('PASS - animated welcome, cache/retry, still-image fallback, preserved account data and access routing.')


if __name__ == '__main__':
    asyncio.run(main())
