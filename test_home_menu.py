"""Offline checks for the public welcome menu and protected access routing."""
import asyncio
import types
from unittest.mock import AsyncMock, patch
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendVideo
import test_signal_flow as H


async def main():
    fake_db = H._install_stub_modules()
    bot_mod = H._load_bot()
    import home_menu
    import config

    rows = bot_mod.build_kb(home_menu.keyboard()).inline_keyboard
    assert [len(row) for row in rows] == [1, 2, 2, 2, 1, 1]
    assert [button.text for row in rows for button in row] == [
        '💎 Get Bot Access', '📖 Guide', '🎯 Test Signals', '⭐ Review',
        '☎️ Support', '▶️ YouTube', '📢 Channel', '📊 Money Management Sheet', '🌐 Change Language']
    assert rows[0][0].callback_data == 'home:access'
    for value in ('@flashhher', 'https://t.me/flashhher'):
        with patch.object(config, 'SUPPORT', value):
            assert home_menu.keyboard()[2][1][1] == 'url:https://t.me/flashhher'
    assert rows[3][1].url == 'https://t.me/goplusaitrader'
    assert rows[3][1].callback_data is None
    assert rows[4][0].callback_data == 'home:sheet'
    assert rows[5][0].callback_data == 'home:language'

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
        assert [c['kind'] for c in sends] == ['video']
        assert sends[0]['asset'] == 'assets/home_banner_loop.mp4'
        assert sends[0]['body'] == home_menu.TEXT
        assert sends[0]['markup'] is not None
        assert sends[0]['parse_mode'] == 'HTML'
        assert fake_db._users[uid]['album_ids'] is None
        assert fake_db._users[uid]['ui_msg_id'] == sends[0]['id']
        assert {k:v for k,v in fake_db._users[uid].items() if k not in ('ui_msg_id', 'album_ids')} == {
            k:v for k,v in initial.items() if k not in ('ui_msg_id', 'album_ids')}
        assert state.state is None

        await bot_mod.home_action(H.FakeCB(uid, 'home:back', 1), bot, state)
        assert bot.calls[-1]['kind'] == 'video'
        assert bot.calls[-1]['asset'] == 'assets/home_banner_loop.mp4'
        assert bot.calls[-1]['body'] == home_menu.TEXT
        assert all(any(c['kind'] == 'delete' and c['id'] == old['id'] for c in bot.calls)
                   for old in sends)

        with patch.object(bot_mod, '_show_menu', AsyncMock()) as menu, \
             patch.object(bot_mod, 'show', AsyncMock()) as show:
            await bot_mod.home_action(H.FakeCB(uid, 'home:access', 1), bot, state)
            if verified:
                menu.assert_awaited_once_with(bot, uid)
                show.assert_not_awaited()
            else:
                show.assert_awaited_once_with(bot, uid, 'register')
                assert state.state == bot_mod.Reg.waiting_uid.state
                menu.assert_not_awaited()

    # New users reach the requested photo immediately with tracked links in
    # both the caption and Register button; typing an ID is already enabled.
    uid = 72020
    fake_db._users[uid] = dict(fake_db._fresh_row(), ref_code='campaign_42')
    bot = H.FakeBot()
    state = H.FakeState()
    with patch.object(bot_mod.asyncio, 'create_task', side_effect=AssertionError('Unexpected registration task')):
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
    assert bot.calls[-1]['kind'] == 'photo'
    assert bot.calls[-1]['asset'] == 'assets/account_id_guide.png'
    assert bot.calls[-1]['body'] == config.MSG_ENTER_UID
    cancel = bot.calls[-1]['markup'].inline_keyboard[0][0]
    assert cancel.text == '❌ Cancel' and cancel.style == 'success'
    assert cancel.callback_data == 'home:back'
    assert state.state == bot_mod.Reg.waiting_uid.state
    await bot_mod.home_action(H.FakeCB(uid, cancel.callback_data, 1), bot, state)
    assert state.state is None and bot.calls[-1]['body'] == home_menu.TEXT
    await bot_mod.enter_registration_uid(H.FakeCB(uid, 'reg:enter_uid', 1), bot, state)
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
    assert bot.calls[-1]['kind'] == 'video' and state.state is None

    # Returning users have the retired promotion removed when Telegram allows it.
    fake_db._users[72030] = dict(fake_db._fresh_row(), nudge_msg_id=7654)
    bot = H.FakeBot()
    await bot_mod.start(H.FakeMessage(72030, '/start'), bot, H.FakeState())
    assert any(call['kind'] == 'delete' and call['id'] == 7654 for call in bot.calls)
    assert fake_db._users[72030]['nudge_msg_id'] is None
    fake_db._users[72030]['nudge_msg_id'] = 7655
    with patch.object(bot_mod.asyncio, 'create_task', side_effect=AssertionError('Unexpected registration task')):
        await bot_mod.nav(H.FakeCB(72030, 'go:register', 1), bot, H.FakeState())
    assert any(call['kind'] == 'delete' and call['id'] == 7655 for call in bot.calls)
    assert fake_db._users[72030]['nudge_msg_id'] is None

    for action in ('tips',):
        cb = H.FakeCB(72000, 'home:' + action, 1)
        bot = H.FakeBot()
        state = H.FakeState()
        state.state = 'registration-in-progress'
        await bot_mod.home_action(cb, bot, state)
        assert not bot.calls
        assert state.state == 'registration-in-progress'
        assert cb.answers == [(home_menu.PENDING[action], True)]

    # Missing/rejected videos keep the caption and buttons on one photo card.
    bot = H.FakeBot()
    with patch.object(bot_mod, 'media_missing', side_effect=lambda key, ext: key == home_menu.VIDEO):
        await bot_mod.start(H.FakeMessage(72010, '/start'), bot, H.FakeState())
    assert bot.calls[-1]['kind'] == 'photo'
    assert bot.calls[-1]['asset'] == 'assets/home_banner.png'
    assert bot.calls[-1]['body'] == home_menu.TEXT and bot.calls[-1]['markup']

    bot = H.FakeBot()
    bot.send_video = AsyncMock(side_effect=TelegramBadRequest(
        method=SendVideo(chat_id=72010, video='bad-id'), message='Invalid video'))
    await bot_mod.start(H.FakeMessage(72010, '/start'), bot, H.FakeState())
    assert bot.calls[-1]['kind'] == 'photo'
    assert bot.calls[-1]['body'] == home_menu.TEXT and bot.calls[-1]['markup']
    assert fake_db._users[72010]['ui_msg_id'] == bot.calls[-1]['id']

    # Neither missing media nor upload failures can hide the welcome controls.
    for missing in (True, False):
        bot = H.FakeBot()
        bot.send_video = AsyncMock(side_effect=RuntimeError('upload unavailable'))
        bot.send_photo = AsyncMock(side_effect=RuntimeError('upload unavailable'))
        with patch.object(bot_mod, 'media_missing', return_value=missing):
            await bot_mod.start(H.FakeMessage(72010, '/start'), bot, H.FakeState())
        sends = [c for c in bot.calls if c['kind'] != 'delete']
        assert len(sends) == 1 and sends[0]['kind'] == 'text'
        assert sends[0]['body'] == home_menu.TEXT and sends[0]['markup']
        assert fake_db._users[72010]['album_ids'] is None

    # Migrate the old split layout by removing both persisted message IDs.
    bot = H.FakeBot()
    fake_db._users[72040] = dict(fake_db._fresh_row(), ui_msg_id=900, album_ids='899')
    await bot_mod.start(H.FakeMessage(72040, '/start'), bot, H.FakeState())
    assert {899, 900}.issubset({c['id'] for c in bot.calls if c['kind'] == 'delete'})
    assert fake_db._users[72040]['album_ids'] is None
    home_ids = {c['id'] for c in bot.calls if c['kind'] != 'delete'}
    reloaded = H._load_bot()
    for action in ('home:language', 'home:access'):
        bot.calls.clear()
        await reloaded.home_action(H.FakeCB(72040, action, 1), bot, H.FakeState())
        assert home_ids.issubset({c['id'] for c in bot.calls if c['kind'] == 'delete'})
        assert fake_db._users[72040]['album_ids'] is None
        bot.calls.clear()
        await reloaded.home_action(H.FakeCB(72040, 'home:back', 1), bot, H.FakeState())
        home_ids = {c['id'] for c in bot.calls if c['kind'] != 'delete'}

    # A signal finishing after /start must not replace the new video card.
    bot = H.FakeBot()
    async def navigate_home(_seconds):
        await bot_mod.start(H.FakeMessage(72041, '/start'), bot, H.FakeState())
    with patch.object(bot_mod.asyncio, 'sleep', navigate_home):
        await bot_mod._run_signal(bot, 72041, 1, 'M1')
    banner = next(c for c in bot.calls if c['kind'] == 'video')
    assert fake_db._users[72041]['ui_msg_id'] == banner['id']
    assert fake_db._users[72041]['album_ids'] is None
    assert H.last_screen(bot)['body'] == home_menu.TEXT

    # The video cache must never reuse a Telegram animation.file_id.
    old_animation = types.SimpleNamespace(animation=types.SimpleNamespace(file_id='old-animation-id'))
    await bot_mod.remember_video(home_menu.ANIMATION, old_animation)
    key = home_menu.VIDEO
    assert bot_mod.cached_id(key, 'mp4') is None
    assert bot_mod.asset_path(key, 'mp4') == 'assets/home_banner_loop.mp4'
    result = types.SimpleNamespace(video=types.SimpleNamespace(file_id='video-file-id'))
    await bot_mod.remember_video(key, result)
    assert bot_mod.cached_id(key, 'mp4') == 'video-file-id'
    assert fake_db._media_cache[key][0] == 'video-file-id'
    assert bot_mod.cached_id(home_menu.ANIMATION, 'mp4') == 'old-animation-id'
    assert key not in bot_mod._photo_cache
    bot = H.FakeBot()
    await bot_mod.start(H.FakeMessage(72011, '/start'), bot, H.FakeState())
    assert bot.calls[-1]['kind'] == 'video'
    assert bot.calls[-1]['asset'] == 'video-file-id'
    assert bot.calls[-1]['body'] == home_menu.TEXT and bot.calls[-1]['markup']

    # An expired cached video retries from the same local MP4 once.
    rejection = TelegramBadRequest(
        method=SendVideo(chat_id=72011, video='video-file-id'), message='Wrong file identifier')
    bot.send_video = AsyncMock(side_effect=[rejection, result])
    await bot_mod.send_media(bot, 72011, key, True, home_menu.TEXT, None)
    calls = bot.send_video.await_args_list
    assert len(calls) == 2 and calls[0].args[1] == 'video-file-id'
    assert str(calls[1].args[1].path) == 'assets/home_banner_loop.mp4'
    assert key not in bot_mod._video_cache

    # Startup warms the new video key using send_video, not send_animation.
    assert (key, 'mp4') in bot_mod.referenced_assets()
    bot = H.FakeBot()
    result.message_id = 900
    bot.send_video = AsyncMock(return_value=result)
    with patch.object(config, 'MEDIA_WARM_CHAT', 72011), \
         patch.object(bot_mod, 'referenced_assets', return_value={(key, 'mp4')}), \
         patch.object(bot_mod.asyncio, 'sleep', AsyncMock()):
        await bot_mod.warm_media_cache(bot)
    bot.send_video.assert_awaited_once()
    assert bot_mod.cached_id(key, 'mp4') == 'video-file-id'
    bot_mod._video_cache.clear()
    print('PASS - single video/caption/menu card, old-layout cleanup, fallbacks, video cache, and access routing.')


if __name__ == '__main__':
    asyncio.run(main())
