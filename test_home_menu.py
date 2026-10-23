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
    assert [len(row) for row in rows] == [1, 1, 2, 2, 1, 1]
    assert [b.callback_data for row in rows for b in row] == [
        'home:test', 'home:access', 'flow:how', 'flow:community',
        'flow:reviews', 'flow:support', 'flow:tools', 'home:language']
    assert [b.style for row in rows for b in row] == [
        'success', 'primary', 'primary', 'primary', 'danger', 'danger', 'primary', 'danger']
    assert [[b.text for b in row] for row in rows] == [
        ['🎯 Try 2 Free Analyses'], ['🚀 Set Up My Account'],
        ['📖 How It Works', '📢 Community'], ['⭐ Reviews', '💬 Support'],
        ['🧰 Tools & FAQ'], ['🌐 Language']]
    for progress in ({'uid': '123456789'}, {'setup_stage': 'requirements'}):
        assert home_menu.keyboard(progress)[1][0][0] == '🚀 Continue Setup'
    assert home_menu.keyboard({'verified': True})[:2] == [
        [('📸 Analyse My Chart', 'cb:home:test', 'success')],
        [('🏠 Open My Dashboard', 'cb:home:access', 'primary')]]
    for verified in (False, True):
        uid = 72000 + int(verified)
        fake_db._users[uid] = dict(fake_db._fresh_row(), verified=verified,
                                   uid='123456789' if verified else None)
        bot, state = H.FakeBot(), H.FakeState()
        await bot_mod.start(H.FakeMessage(uid, '/start'), bot, state)
        sends = [c for c in bot.calls if c['kind'] != 'delete']
        assert len(sends) == 1 and sends[0]['kind'] == 'video'
        assert sends[0]['asset'] == 'assets/home_banner_loop.mp4'
        assert sends[0]['body'] == home_menu.TEXT and sends[0]['markup']
        assert state.state is None
        await bot_mod.home_action(H.FakeCB(uid, 'home:access', 1), bot, state)
        if verified:
            assert 'menu:signal' in [b.callback_data for row in bot.calls[-1]['markup'].inline_keyboard for b in row]
        else:
            assert 'qualifying deposits' in bot.calls[-1]['body']
            assert fake_db._users[uid]['setup_stage'] == 'requirements'
        await bot_mod.nav(H.FakeCB(uid, 'go:guide', 1), bot, state)
        assert bot.calls[-1]['asset'] == 'assets/guide_opPxk6Af.mp4'
        await bot_mod.home_action(H.FakeCB(uid, 'home:back', 1), bot, state)
        assert bot.calls[-1]['body'] == home_menu.TEXT

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
        pinned = fake_db._users[72040]['welcome_message_id']
        deleted = {c['id'] for c in bot.calls if c['kind'] == 'delete'}
        assert (home_ids - {pinned}).issubset(deleted)
        assert pinned not in deleted
        assert fake_db._users[72040]['album_ids'] is None
        bot.calls.clear()
        await reloaded.home_action(H.FakeCB(72040, 'home:back', 1), bot, H.FakeState())
        home_ids = {c['id'] for c in bot.calls if c['kind'] != 'delete'}

    # A signal finishing after /start must not replace the new welcome card.
    bot = H.FakeBot()
    async def navigate_home(_seconds):
        await bot_mod.start(H.FakeMessage(72041, '/start'), bot, H.FakeState())
    with patch.object(bot_mod.asyncio, 'sleep', navigate_home):
        await bot_mod._run_signal(bot, 72041, 1, 'M1')
    banner = next(c for c in bot.calls if c['kind'] == 'video')
    assert fake_db._users[72041]['ui_msg_id'] == banner['id']
    assert fake_db._users[72041]['album_ids'] is None
    assert H.last_screen(bot)['body'] == home_menu.TEXT

    # The video cache must never reuse the previous animation.file_id.
    old_animation = types.SimpleNamespace(animation=types.SimpleNamespace(file_id='old-animation-id'))
    await bot_mod.remember_video(home_menu.ANIMATION, old_animation)
    key = home_menu.VIDEO
    # Persisted IDs from the old artwork must force a fresh upload.
    bot_mod._video_cache[key] = ('stale-video-id', 'old-artwork-hash')
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

    # Startup warms the video key, with the new artwork's dimensions.
    assert (key, 'mp4') in bot_mod.referenced_assets()
    assert (config.SCREENS['guide']['video'], 'mp4') in bot_mod.referenced_assets()
    bot = H.FakeBot()
    result.message_id = 900
    bot.send_video = AsyncMock(return_value=result)
    with patch.object(config, 'MEDIA_WARM_CHAT', 72011), \
         patch.object(bot_mod, 'referenced_assets', return_value={(key, 'mp4')}), \
         patch.object(bot_mod.asyncio, 'sleep', AsyncMock()):
        await bot_mod.warm_media_cache(bot)
    bot.send_video.assert_awaited_once()
    assert bot.send_video.await_args.kwargs == {'width': 1280, 'height': 720, 'duration': 6, 'supports_streaming': True}
    assert bot_mod.cached_id(key, 'mp4') == 'video-file-id'
    bot_mod._video_cache.clear()
    # Only the preview is square. Warm uploads, cached sends and expired-ID
    # retries all attach the cover while preserving the original video size.
    guide_key = config.SCREENS['guide']['video']
    result = types.SimpleNamespace(video=types.SimpleNamespace(file_id='portrait-guide-id'), message_id=900)
    bot.send_video = AsyncMock(return_value=result)
    with patch.object(config, 'MEDIA_WARM_CHAT', 72011), \
         patch.object(bot_mod, 'referenced_assets', return_value={(guide_key, 'mp4')}), \
         patch.object(bot_mod.asyncio, 'sleep', AsyncMock()):
        await bot_mod.warm_media_cache(bot)
    warm_options = dict(bot.send_video.await_args.kwargs)
    assert str(warm_options.pop('cover').path) == 'assets/guide_opPxk6Af_cover.jpg'
    assert warm_options == {'width': 1080, 'height': 1920, 'duration': 99}
    assert bot_mod.cached_id(guide_key, 'mp4') == 'portrait-guide-id'
    bot.send_video.reset_mock()
    await bot_mod.send_media(bot, 72011, guide_key, True, config.SCREENS['guide']['text'], None)
    bot.send_video.assert_awaited_once()
    assert bot.send_video.await_args.args[1] == 'portrait-guide-id'
    assert str(bot.send_video.await_args.kwargs['cover'].path) == 'assets/guide_opPxk6Af_cover.jpg'
    bot.send_video = AsyncMock(side_effect=[rejection, result])
    await bot_mod.send_media(bot, 72011, guide_key, True, config.SCREENS['guide']['text'], None)
    assert bot.send_video.await_args_list[0].args[1] == 'portrait-guide-id'
    assert str(bot.send_video.await_args_list[1].args[1].path) == 'assets/guide_opPxk6Af.mp4'
    for call in bot.send_video.await_args_list:
        assert (call.kwargs['width'], call.kwargs['height'], call.kwargs['duration']) == (1080, 1920, 99)
        assert str(call.kwargs['cover'].path) == 'assets/guide_opPxk6Af_cover.jpg'
    print('PASS - single video/caption/menu card, old-layout cleanup, fallbacks, separate media caches, and access routing.')


if __name__ == '__main__':
    asyncio.run(main())
