"""End-to-end chat navigation with recorded Telegram transport and panel responses."""
import asyncio
import os
from decimal import Decimal
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch
os.environ.setdefault('DATABASE_URL', 'postgresql://unused')
import config
import journey
import test_signal_flow as H


class JourneyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = H._install_stub_modules()
        self.mod = H._load_bot()
        self.bot, self.state = H.FakeBot(), H.FakeState()
        self.db._users[91] = self.row = self.db._fresh_row()

    async def tap(self, action):
        await self.mod.flow_action(H.FakeCB(91, 'flow:' + action, 1), self.bot, self.state)
        return [c for c in self.bot.calls if c['kind'] != 'delete'][-1]

    async def test_new_account_requirements_eligibility_then_registration(self):
        card = await self.tap('setup')
        self.assertIn('$50.00', card['body'])
        self.assertIn('commission', card['body'])
        await self.tap('new')
        self.assertIsNone(self.row['eligibility_confirmed_at'])
        with patch.object(self.mod, '_ref_url', AsyncMock(return_value='https://pocketoption.com/?a=test')):
            card = await self.tap('eligible')
        self.assertEqual(self.row['setup_stage'], 'register')
        self.assertIn('url', card['markup'].inline_keyboard[0][0].model_dump())
        await self.tap('enter_id')
        self.assertEqual(self.state.state, self.mod.Reg.waiting_uid.state)

    async def test_no_registration_or_verification_before_eligibility(self):
        with patch.object(self.mod, '_verify_once', AsyncMock()) as verify:
            await self.mod._submit_uid(91, '123456789', self.bot, self.state)
            verify.assert_not_awaited()
        await self.tap('new')
        await self.tap('ineligible')
        self.assertIsNone(self.row['eligibility_confirmed_at'])
        self.assertIsNone(self.row['uid'])
        self.assertNotIn('Register on', self.bot.calls[-1]['body'])

    async def test_existing_or_saved_account_never_gets_signup_link(self):
        for saved in (None, '123456789'):
            self.row.update(uid=saved, eligibility_confirmed_at='now', account_kind='existing')
            card = await self.tap('new' if saved else 'existing')
            self.assertEqual(self.row['account_kind'], 'existing')
            self.assertIn('existing account', card['body'])
            self.assertFalse(any(b.url for row in card['markup'].inline_keyboard for b in row))

    async def test_saved_id_deposit_retry_and_restart_resume(self):
        self.row.update(uid='123456789', account_kind='existing', eligibility_confirmed_at='now')
        with patch.object(self.mod, '_panel_verdict', AsyncMock(return_value=(config.VERIFY_NEED_DEPOSIT, Decimal('12.34')))):
            card = await self.tap('recheck')
        self.assertIn('$37.66', card['body'])
        self.assertFalse(self.row['verified'])
        self.assertEqual(self.row['setup_stage'], 'deposit')
        self.assertEqual(card['markup'].inline_keyboard[0][0].url, config.BROKER_ACCOUNT_URL)
        self.mod = H._load_bot()  # No in-memory setup state is required to resume.
        await self.mod._resume_setup(self.bot, 91)
        self.assertIn('$37.66', self.bot.calls[-1]['body'])
        with patch.object(self.mod, '_panel_verdict', AsyncMock(return_value=(config.VERIFY_GRANTED, Decimal('50')))) as lookup:
            await self.tap('recheck')
        lookup.assert_awaited_once_with(91, '123456789')
        self.assertTrue(self.row['verified'])
        self.assertEqual(self.row['setup_stage'], 'active')
        self.assertTrue(any(b.callback_data == 'menu:signal' for row in self.bot.calls[-1]['markup'].inline_keyboard for b in row))

    async def test_unavailable_and_unlinked_are_not_missing_deposits(self):
        self.row.update(uid='123456789', eligibility_confirmed_at='now')
        for status in (config.VERIFY_TEMPORARY, config.VERIFY_WRONG_LINK):
            self.mod._uid_lookup_at.clear()
            with patch.object(self.mod, '_panel_verdict', AsyncMock(return_value=(status, None))), \
                 patch.object(config, 'UID_VERIFY_RETRIES', 0):
                await self.tap('recheck')
            self.assertFalse(self.row['verified'])
            self.assertEqual(self.row['access_check_status'], status)
            self.assertIsNone(self.row['access_check_deposit'])
            self.assertFalse(any(b.url == config.BROKER_ACCOUNT_URL for row in self.bot.calls[-1]['markup'].inline_keyboard for b in row))

    async def test_all_information_screens_render_real_buttons(self):
        for name in journey.TEXTS:
            card = await self.tap(name)
            self.assertTrue(card['body'] and card['markup'], name)
            for row in card['markup'].inline_keyboard:
                for button in row:
                    self.assertTrue(button.callback_data or journey.safe_url(button.url), name)
                    if button.callback_data:
                        self.assertLessEqual(len(button.callback_data.encode()), 64)
        card = await self.tap('example')
        self.assertEqual(card['kind'], 'photo')
        self.assertTrue(Path(card['asset']).is_file())

    async def test_notifications_and_verified_navigation_preserve_access(self):
        self.row.update(verified=True, uid='123456789', signals_used_today=7,
                        last_reset_date=self.db._today[0])
        for action in ('notifications_off', 'notifications_on', 'usage', 'compare', 'dashboard'):
            await self.tap(action)
            self.assertTrue(self.row['verified'])
            self.assertEqual(self.row['signals_used_today'], 7)
            if action.startswith('notifications_'):
                self.assertEqual(self.row['broadcast_opt_out'], action.endswith('off'))

    async def test_missing_link_has_working_support_fallback(self):
        for value in ('', 'https://example.com/PLACEHOLDER_REF', 'javascript:alert(1)', 'https://user:pass@example.org'):
            self.assertEqual(journey.url('Register', value)[1], 'cb:flow:link_unavailable')
        for value in ('@flashhher', 'https://t.me/flashhher'):
            with patch.object(config, 'SUPPORT', value):
                self.assertEqual(journey.support_url(), 'https://t.me/flashhher')

    async def test_saved_premium_check_requires_live_account_bound_verdict(self):
        self.row.update(verified=True, uid='123456789', deposit=Decimal('999'))
        with patch.object(self.mod, '_panel_verdict', AsyncMock(return_value=(config.VERIFY_NEED_DEPOSIT, Decimal('99.99')))) as lookup:
            card = await self.tap('premium_check')
        lookup.assert_awaited_once_with(91, '123456789', minimum_deposit=config.PREMIUM_MIN_DEPOSIT)
        self.assertIn('$0.01', card['body'])
        self.assertFalse(self.row['is_premium'])
        self.assertFalse(self.mod._premium_checks)

    async def test_missing_or_invalid_deposit_is_pending_never_confirmed_zero(self):
        for amount in (None, 'NaN', 'Infinity', '-1', 'unavailable'):
            with patch.object(self.mod.panelbot, 'lookup_trader', AsyncMock(return_value={
                    'campaign_id': config.CAMPAIGN_ID, 'sum_deposits': amount})):
                status, detail = await self.mod._panel_verdict(91, '123456789')
            self.assertEqual(status, config.VERIFY_TEMPORARY)
        with patch.object(self.mod.panelbot, 'lookup_trader', AsyncMock(return_value={
                'campaign_id': config.CAMPAIGN_ID, 'sum_deposits': Decimal(0)})):
            status, amount = await self.mod._panel_verdict(91, '123456789')
        self.assertEqual((status, amount), (config.VERIFY_NEED_DEPOSIT, Decimal(0)))

    async def test_preview_mode_cannot_bypass_verification_for_public_users(self):
        self.row.update(eligibility_confirmed_at='now', account_kind='existing')
        with patch.object(config, 'TEST_MODE', True), patch.object(config, 'ADMIN_IDS', []), \
             patch.object(self.mod, '_verify_once', AsyncMock(return_value=config.VERIFY_TEMPORARY)) as check:
            await self.mod._submit_uid(91, '123456789', self.bot, self.state)
        check.assert_awaited_once()
        self.assertFalse(self.row['verified'])


if __name__ == '__main__':
    unittest.main()
