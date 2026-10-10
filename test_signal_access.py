"""Offline regression: old signal callbacks cannot bypass the lifetime trial."""
import unittest
from unittest.mock import AsyncMock, patch

import test_signal_flow as H


class SignalAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = H._install_stub_modules()
        self.mod = H._load_bot()
        self.tg_id = 11
        self.row = self.db._fresh_row()
        self.row['ui_msg_id'] = 700
        self.db._users[self.tg_id] = self.row
        self.bot = H.FakeBot()
        self.register = self.mock(self.mod, '_show_register')
        self.quota = self.mock(self.db, 'signal_state', return_value=(0, 30))
        self.consume = self.mock(self.db, 'consume_signal', return_value=(True, 1, 29))

    def mock(self, obj, name, **kwargs):
        p = patch.object(obj, name, AsyncMock(**kwargs))
        value = p.start()
        self.addCleanup(p.stop)
        return value

    async def blocked_callbacks(self):
        callbacks = [('menu:signal', self.mod.menu_signal),
                     ('new_signal', self.mod.new_signal)]
        callbacks += [('m:%d' % n, self.mod.m_action) for n in range(1, 11)]
        for data, handler in callbacks:
            with self.subTest(data=data):
                self.register.reset_mock()
                cb = H.FakeCB(self.tg_id, data, 700)
                if data == 'menu:signal':
                    state = H.FakeState('old-state')
                    await handler(cb, self.bot, state)
                    self.assertIsNone(state.state)
                else:
                    await handler(cb, self.bot)
                self.assertEqual(cb.answers, [(None, False)])
                self.register.assert_awaited_once_with(self.bot, self.tg_id)
                self.assertNotIn(self.tg_id, self.mod._signal_tasks)
        self.quota.assert_not_awaited()
        self.consume.assert_not_awaited()
        self.assertEqual(self.bot.calls, [])

    async def test_unverified_old_buttons_block_even_with_premium_and_tokens(self):
        self.row.update(is_premium=True, game_tokens=1000)
        before = dict(self.row)
        await self.blocked_callbacks()
        self.assertEqual(self.row, before)

    async def test_missing_user_is_not_granted_signals(self):
        self.mock(self.db, 'get_user', return_value=None)
        await self.blocked_callbacks()

    async def test_start_and_new_day_do_not_unlock_manual_signals(self):
        self.mock(self.mod, 'render')
        self.mock(self.mod, '_clear_nudge')
        self.mock(self.mod, '_capture_ref')
        await self.mod.start(H.FakeMessage(self.tg_id, '/start'), self.bot, H.FakeState())
        self.db._today[0] = '2026-10-11'
        await self.blocked_callbacks()
        self.assertFalse(self.row['verified'])

    async def test_verified_menu_opens_chart_upload_and_old_callbacks_stay_protected(self):
        self.row['verified'] = True
        show = self.mock(self.mod, 'show')
        chart = self.mock(self.mod.chart_signals, 'open_screen')
        pairs = self.mock(self.mod, 'show_pairs')
        run = self.mock(self.mod, '_run_signal')
        cb = H.FakeCB(self.tg_id, 'menu:signal', 700)
        state = H.FakeState()
        await self.mod.menu_signal(cb, self.bot, state)
        chart.assert_awaited_once_with(cb, self.bot, state, self.mod.render, upload=True)
        show.assert_not_awaited()
        run.assert_not_awaited()
        await self.mod.new_signal(H.FakeCB(self.tg_id, 'new_signal', 700), self.bot)
        pairs.assert_awaited_once_with(self.bot, self.tg_id, 0)
        await self.mod.m_action(H.FakeCB(self.tg_id, 'm:5', 700), self.bot)
        await self.mod._signal_tasks.pop(self.tg_id)
        run.assert_awaited_once_with(self.bot, self.tg_id, 700, 'M5')
        self.register.assert_not_awaited()

    async def test_revoked_verification_during_countdown_prevents_delivery(self):
        self.row['verified'] = True
        self.mock(self.mod, '_send_wait_screen', return_value=(700, [701, 702]))
        self.mock(self.mod, '_drop_msgs')
        render = self.mock(self.mod, 'render')

        async def revoke(_delay):
            self.row['verified'] = False

        with patch.object(self.mod.asyncio, 'sleep', AsyncMock(side_effect=revoke)):
            await self.mod.m_action(H.FakeCB(self.tg_id, 'm:1', 700), self.bot)
            await self.mod._signal_tasks[self.tg_id]
        self.register.assert_awaited_once_with(self.bot, self.tg_id)
        self.consume.assert_not_awaited()
        render.assert_not_awaited()
        self.assertNotIn(self.tg_id, self.mod._signal_tasks)


if __name__ == '__main__':
    unittest.main()
