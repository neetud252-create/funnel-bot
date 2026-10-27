"""Normal /start, menus and admin reset with no Elena session or contact step."""
import asyncio
import copy
import os
from unittest.mock import AsyncMock, patch
import test_signal_flow as H


async def main():
    db = H._install_stub_modules()
    B = H._load_bot()
    bot = H.FakeBot()
    bot.id = 8790198817
    with patch.dict(os.environ, ELENA_ENABLED='0', ELENA_ACTIVATION_ENABLED='0'), \
         patch.object(B.elena.store, 'session', AsyncMock(side_effect=AssertionError('No Elena session needed'))), \
         patch.object(B.elena.store, 'reset_activation', AsyncMock()) as reset_activation, \
         patch.object(B.elena.store, 'activation_opened', AsyncMock()) as opened, \
         patch.object(B, '_show_activation_gate', AsyncMock()) as gate:
        for tg_id in (85001, 85002, 85003):
            if tg_id != 85001:
                H._dirty_user(db, tg_id)
                db._users[tg_id] = db._fresh_row() | db._users[tg_id]
                db._users[tg_id]['verified'] = tg_id == 85003
            state = H.FakeState(state='Reg:waiting_uid')
            await B.start(H.FakeMessage(tg_id, '/start'), bot, state)
            assert state.state is None
            if tg_id == 85003:
                assert 'main menu' in bot.calls[-1]['body'].lower()
                assert db._users[tg_id]['verified'] is True
            else:
                assert bot.calls[-1]['asset'] == 'assets/home_banner_loop.mp4'
                assert 'Try the demo with 1 free signal' in bot.calls[-1]['body']
                buttons = [b for row in bot.calls[-1]['markup'].inline_keyboard for b in row]
                assert any(b.callback_data == 'home:test' for b in buttons)
                assert any(b.callback_data == 'home:access' for b in buttons)
            gate.assert_not_awaited()

        # Existing menu callbacks pass the former gate, without granting trading access.
        event = H.FakeMessage(85002, 'anything')
        event.from_user.is_bot = False
        handler = AsyncMock(return_value='existing menu')
        assert await B.activation.GateMiddleware()(handler, event, {}) == 'existing menu'
        handler.assert_awaited_once()
        assert not db._users[85002]['verified']
        assert not await B._open_from_elena(bot, 85002)

        # /devstart retains the actual account/quota reset and goes directly home.
        B.config.ADMIN_IDS = [85003]
        untouched = copy.deepcopy(db._users[85002])
        original_reset = db.reset_user
        with patch.object(db, 'reset_user', wraps=original_reset) as reset:
            await B.cmd_devstart(H.FakeMessage(85003, '/devstart'), bot, H.FakeState())
            reset.assert_awaited_once_with(85003)
        assert not db._users[85003]['verified'] and db._users[85003]['uid'] is None
        assert bot.calls[-1]['asset'] == 'assets/home_banner_loop.mp4'
        assert db._users[85002] == untouched
        reset_activation.assert_not_awaited()
        opened.assert_not_awaited()
        gate.assert_not_awaited()
    print('PASS: new/existing/verified /start, normal callbacks and /devstart without Elena')


if __name__ == '__main__':
    asyncio.run(main())
