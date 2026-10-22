"""Offline Premium eligibility and quota regression; no live accounts or money."""
import asyncio
import importlib.util
import os
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import config
import chart_trial
import test_signal_flow as H


class PremiumDepositTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = H._install_stub_modules()
        self.botmod = H._load_bot()
        self.row = dict(self.db._fresh_row(), verified=True, uid='123456789',
                        deposit=Decimal('20'), game_tokens=1000,
                        signals_used_today=30, last_reset_date=self.db._today[0])
        self.db._users[11] = self.row
        self.bot = H.FakeBot()
        self.state = H.FakeState(self.botmod.Premium.waiting_uid)
        self.lookup = AsyncMock(return_value={
            'campaign_id': config.CAMPAIGN_ID, 'sum_deposits': Decimal('100')})
        self.patches = [patch.object(self.botmod.panelbot, 'lookup_trader', self.lookup),
                        patch.object(config, 'PREMIUM_MIN_DEPOSIT', Decimal('100')),
                        patch.object(config, 'PREMIUM_DAILY_SIGNALS', 70)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    async def send(self, uid='123456789'):
        await self.botmod.premium_uid(H.FakeMessage(11, uid), self.bot, self.state)
        return [c['body'] for c in self.bot.calls if c['kind'] != 'delete'][-1]

    async def test_exact_100_unlocks_preserves_usage_tokens_and_linked_id(self):
        text = await self.send()
        self.assertTrue(self.row['is_premium'])
        self.assertTrue('70 AI signals per day' in text or '70 analyses/day' in text)
        self.assertEqual(self.row['deposit'], Decimal('100'))
        self.assertEqual(self.row['uid'], '123456789')
        self.assertEqual(self.row['game_tokens'], 1000)
        self.assertEqual(await self.db.signal_state(11, 70), (30, 40))
        self.assertIsNone(self.state.state)
        self.assertEqual(await self.botmod._user_quota(11), (True, 70))
        await self.botmod.menu_level(H.FakeCB(11, 'menu:level', 1), self.bot)
        text = self.bot.calls[-1]['body']
        for expected in ('Premium', '70 signals', '30', '40'):
            self.assertIn(expected, text)
        self.assertNotIn('Game tokens', text)

    async def test_above_100_and_repeated_unlock_do_not_reset_usage(self):
        self.lookup.return_value['sum_deposits'] = Decimal('125.50')
        await self.send()
        text = await self.send()
        self.assertIn('already active', text)
        self.assertEqual(self.lookup.await_count, 1)
        self.assertEqual(self.row['signals_used_today'], 30)

    async def test_99_99_fails_despite_cached_deposit_and_tokens_then_retry_succeeds(self):
        self.row['deposit'] = Decimal('500')
        self.lookup.return_value['sum_deposits'] = Decimal('99.99')
        text = await self.send()
        self.assertFalse(self.row['is_premium'])
        self.assertIn('$99.99', text)
        self.assertIn('$0.01', text)
        self.assertIsNotNone(self.state.state)
        self.lookup.return_value['sum_deposits'] = Decimal('100')
        await self.send()
        self.assertTrue(self.row['is_premium'])
        self.assertEqual(self.lookup.await_count, 2)

    async def test_wrong_campaign_or_unknown_account_cannot_unlock(self):
        for result in (None, {'campaign_id': 'wrong', 'sum_deposits': Decimal('500')}):
            self.lookup.return_value = result
            text = await self.send()
            self.assertFalse(self.row['is_premium'])
            self.assertIn('could not confirm access', text)
            actions = [b.callback_data for row in self.bot.calls[-1]['markup'].inline_keyboard for b in row]
            self.assertIn('flow:premium_check', actions)
            self.assertNotIn('flow:enter_id', actions)

    async def test_invalid_or_different_id_cannot_use_another_deposit(self):
        for uid in ('not-an-id', '987654321'):
            await self.send(uid)
            self.assertFalse(self.row['is_premium'])
        self.lookup.assert_not_awaited()

    async def test_unverified_user_cannot_open_or_use_premium_flow(self):
        self.row['verified'] = False
        with patch.object(self.botmod, '_open_registration', AsyncMock()) as register:
            await self.botmod.menu_premium(H.FakeCB(11, 'menu:premium', 1), self.bot, self.state)
            await self.botmod.premium_uid(H.FakeMessage(11, '123456789'), self.bot, self.state)
            self.assertEqual(register.await_count, 2)
        self.lookup.assert_not_awaited()
        self.assertFalse(self.row['is_premium'])

    async def test_unavailable_timeout_or_invalid_amount_never_grants(self):
        for exc in (self.botmod.panelbot.PanelUnavailable('timeout'), TimeoutError(), ValueError()):
            self.lookup.side_effect = exc
            text = await self.send()
            self.assertIn('cannot confirm', text)
            self.assertFalse(self.row['is_premium'])
            self.assertIsNotNone(self.state.state)
            self.assertFalse(self.botmod._premium_checks)
        self.lookup.side_effect = None
        self.lookup.return_value['sum_deposits'] = Decimal('NaN')
        text = await self.send()
        self.assertIn('cannot confirm', text)
        self.assertFalse(self.row['is_premium'])

    async def test_test_mode_still_requires_a_real_100_deposit(self):
        self.lookup.return_value['sum_deposits'] = Decimal('20')
        with patch.object(config, 'TEST_MODE', True):
            await self.send()
        self.lookup.assert_awaited_once()
        self.assertFalse(self.row['is_premium'])

    async def test_concurrent_messages_make_one_panel_lookup(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def lookup(uid):
            started.set()
            await release.wait()
            return {'campaign_id': config.CAMPAIGN_ID, 'sum_deposits': Decimal('100')}
        self.lookup.side_effect = lookup
        first = asyncio.create_task(self.send())
        await started.wait()
        await self.send()
        release.set()
        await first
        self.assertEqual(self.lookup.await_count, 1)
        self.assertTrue(self.row['is_premium'])

    async def test_account_changed_during_check_cannot_unlock(self):
        async def lookup(uid):
            self.row['uid'] = '987654321'
            return {'campaign_id': config.CAMPAIGN_ID, 'sum_deposits': Decimal('100')}
        self.lookup.side_effect = lookup
        await self.send()
        self.assertFalse(self.row['is_premium'])

    async def test_menu_uses_deposit_requirement_and_70_limit(self):
        await self.botmod.menu_premium(H.FakeCB(11, 'menu:premium', 1), self.bot, self.state)
        text = self.bot.calls[-1]['body']
        self.assertIn('$100', text)
        self.assertTrue('70 AI signals per day' in text or '70 analyses/day' in text)
        self.assertNotIn('tokens', text)
        self.assertFalse(self.row['is_premium'])

    async def test_shared_chart_status_uses_70_and_existing_counter(self):
        await self.send()
        today = self.db._today[0]
        conn = SimpleNamespace(fetchrow=AsyncMock(return_value={**self.row, 'today': today}),
                               fetchval=AsyncMock(return_value=2))
        self.assertEqual(await chart_trial._status(conn, 11), (True, 38))
        self.row['signals_used_today'] = 69
        self.assertEqual(await self.db.consume_signal(11, 70), (True, 70, 0))
        self.assertEqual(await self.db.consume_signal(11, 70), (False, 70, 0))
        self.db._today[0] = '2026-08-26'
        self.assertEqual(await self.db.signal_state(11, 70), (0, 70))


class DepositDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        os.environ.setdefault('DATABASE_URL', 'postgresql://unused')
        spec = importlib.util.spec_from_file_location('premium_db_under_test', Path(__file__).with_name('db.py'))
        self.db = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.db)
        self.conn = SimpleNamespace(fetchrow=AsyncMock(return_value={'is_premium': True}))
        conn = self.conn
        class Acquire:
            async def __aenter__(self): return conn
            async def __aexit__(self, *args): pass
        self.db.pool = SimpleNamespace(acquire=lambda: Acquire())

    async def test_validated_amount_passed_to_atomic_account_bound_update(self):
        self.assertTrue(await self.db.unlock_premium(11, '123456789', Decimal('100'), Decimal('100')))
        args = self.conn.fetchrow.await_args.args
        self.assertEqual(args[1:], (11, '123456789', Decimal('100'), Decimal('100')))
        for invalid in ('99.99', '-1', 'NaN', 'Infinity', None, 'oops'):
            self.assertFalse(await self.db.unlock_premium(11, '123456789', invalid, 100))
        self.assertEqual(self.conn.fetchrow.await_count, 1)
        self.conn.fetchrow.return_value = None
        self.assertFalse(await self.db.unlock_premium(11, 'changed-id', 100, 100))


if __name__ == '__main__':
    unittest.main()
