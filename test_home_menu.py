"""Offline checks for the public welcome menu and protected access routing."""
import asyncio
import types
from unittest.mock import AsyncMock, patch
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
        assert len(sends) == 1 and sends[0]['kind'] == 'photo'
        assert sends[0]['asset'] == 'assets/home_banner.png'
        assert sends[0]['body'] == home_menu.TEXT
        assert sends[0]['markup'] is not None
        assert {k:v for k,v in fake_db._users[uid].items() if k!='ui_msg_id'} == {
            k:v for k,v in initial.items() if k!='ui_msg_id'}
        assert state.state is None

        await bot_mod.home_action(H.FakeCB(uid, 'home:back', 1), bot, state)
        assert bot.calls[-1]['kind'] == 'photo'
        assert bot.calls[-1]['asset'] == 'assets/home_banner.png'
        assert bot.calls[-1]['body'] == home_menu.TEXT

        with patch.object(bot_mod, '_show_menu', AsyncMock()) as menu, \
             patch.object(bot_mod, 'show', AsyncMock()) as show:
            await bot_mod.home_action(H.FakeCB(uid, 'home:access', 1), bot, state)
            if verified:
                menu.assert_awaited_once_with(bot, uid)
                show.assert_not_awaited()
            else:
                show.assert_awaited_once_with(bot, uid, 'gate')
                menu.assert_not_awaited()

    for action in ('test', 'tips'):
        cb = H.FakeCB(72000, 'home:' + action, 1)
        bot = H.FakeBot()
        state = H.FakeState()
        state.state = 'registration-in-progress'
        await bot_mod.home_action(cb, bot, state)
        assert not bot.calls
        assert state.state == 'registration-in-progress'
        assert cb.answers == [(home_menu.PENDING[action], True)]
    print('PASS - eight-button welcome, preserved account/referral data, access gate and pending buttons.')


if __name__ == '__main__':
    asyncio.run(main())
