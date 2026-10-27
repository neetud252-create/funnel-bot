"""Deposit media, saved-ID checks and isolated admin urgency preview. No network."""
import asyncio
from decimal import Decimal
import unittest
from unittest.mock import AsyncMock, patch
import test_signal_flow as H


class DepositTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = H._install_stub_modules()
        self.B = H._load_bot()
        self.bot = H.FakeBot()
        self.admin, self.customer = 85001, 85002
        self.admins = patch.object(self.B.config, 'ADMIN_IDS', [self.admin])
        self.admins.start()
        self.addCleanup(self.admins.stop)
        self.minimum = patch.object(self.B.config, 'MIN_DEPOSIT', Decimal('20'))
        self.minimum.start()
        self.addCleanup(self.minimum.stop)
        for uid in (self.admin, self.customer):
            self.db._users[uid] = dict(self.db._fresh_row(), uid='123456789', setup_stage='deposit',
                access_check_status=self.B.config.VERIFY_NEED_DEPOSIT, access_check_deposit=Decimal('0'))

    async def asyncTearDown(self):
        tasks = list(self.B._deposit_nudge_tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def test_customer_photo_exact_caption_and_four_actions(self):
        await self.B._resume_setup(self.bot, self.customer)
        card = H.last_screen(self.bot)
        self.assertEqual(card['asset'], 'assets/deposit_account.png')
        self.assertEqual(card['body'],
            '✅ <b>Great! Your Pocket IQ registration is complete!</b>\n\n'
            '🧾 Just one step left — make a <b>deposit of at least $20</b> into your Pocket Option account.\n\n'
            '🎁 Use promo code YEB789 to get a 50% bonus on your first deposit.\n\n'
            '🚀 Once your deposit is confirmed, Pocket IQ will be ready to activate.\n\n'
            '👇 Already deposited? Click the <b>“I’ve deposited”</b> button below.')
        rows = card['markup'].inline_keyboard
        self.assertEqual([len(row) for row in rows], [1, 1, 1, 1])
        self.assertEqual([row[0].style for row in rows], ['primary', 'primary', 'primary', 'success'])
        self.assertEqual(rows[0][0].url, self.B.config.BROKER_ACCOUNT_URL)
        self.assertEqual(rows[1][0].callback_data, 'flow:deposit_guide')
        self.assertEqual(rows[2][0].url, self.B.journey.support_url())
        self.assertEqual(rows[3][0].callback_data, 'flow:recheck')
        self.assertFalse(self.B._deposit_nudge_tasks, 'Customer receives no test scarcity claim')
        self.assertIn((self.B.journey.DEPOSIT_PHOTO, 'jpg'), self.B.referenced_assets())

    async def test_guide_and_back_keep_saved_uid(self):
        for action in ('deposit_guide', 'setup'):
            await self.B.flow_action(H.FakeCB(self.customer, 'flow:' + action, 1), self.bot, H.FakeState())
            self.assertEqual(H.last_screen(self.bot)['asset'], 'assets/deposit_account.png')
            self.assertEqual(self.db._users[self.customer]['uid'], '123456789')
        with patch.object(self.B, '_panel_verdict', AsyncMock(return_value=(
                self.B.config.VERIFY_GRANTED, Decimal('20')))) as check:
            await self.B.flow_action(H.FakeCB(self.customer, 'flow:recheck', 1), self.bot, H.FakeState())
        check.assert_awaited_once_with(self.customer, '123456789')
        self.assertTrue(self.db._users[self.customer]['verified'])

    async def test_admin_preview_is_delayed_and_removed_when_leaving(self):
        started, release, delays = asyncio.Event(), asyncio.Event(), []
        async def wait(seconds):
            delays.append(seconds)
            started.set()
            await release.wait()
        with patch.object(self.B.asyncio, 'sleep', wait):
            await self.B._show_deposit(self.bot, self.admin, 0)
            task = self.B._deposit_nudge_tasks[self.admin]
            await started.wait()
            self.assertEqual(delays, [2])
            self.assertNotEqual(H.last_screen(self.bot)['body'], self.B.journey.DEPOSIT_TEST_URGENCY)
            release.set()
            await task
        self.assertEqual(H.last_screen(self.bot)['body'], self.B.journey.DEPOSIT_TEST_URGENCY)
        nudge_id = self.db._users[self.admin]['nudge_msg_id']
        self.assertIsNotNone(nudge_id)
        await self.B._show_home(self.bot, self.admin)
        self.assertIn(nudge_id, [c['id'] for c in self.bot.calls if c['kind'] == 'delete'])
        self.assertIsNone(self.db._users[self.admin]['nudge_msg_id'])

    async def test_navigation_cancels_pending_preview(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def wait(seconds):
            started.set()
            await release.wait()
        with patch.object(self.B.asyncio, 'sleep', wait):
            await self.B._show_deposit(self.bot, self.admin, 0)
            task = self.B._deposit_nudge_tasks[self.admin]
            await started.wait()
            await self.B._show_home(self.bot, self.admin)
            await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(task.cancelled())
        self.assertFalse(any(c.get('body') == self.B.journey.DEPOSIT_TEST_URGENCY for c in self.bot.calls))

    async def test_changed_status_or_user_cannot_receive_stale_preview(self):
        row = self.db._users[self.admin]
        base = dict(row, ui_msg_id=44)
        for change in ({'verified': True}, {'uid': '999999999'}, {'ui_msg_id': 45},
                       {'access_check_status': self.B.config.VERIFY_WRONG_LINK}):
            row.clear()
            row.update(base | change)
            with patch.object(self.B.asyncio, 'sleep', AsyncMock()):
                await self.B._send_deposit_test_nudge(self.bot, self.admin, 44, '123456789')
        self.db._users[self.customer]['ui_msg_id'] = 44
        with patch.object(self.B.asyncio, 'sleep', AsyncMock()):
            await self.B._send_deposit_test_nudge(self.bot, self.customer, 44, '123456789')
        self.assertFalse(self.bot.calls)

    async def test_photo_failure_keeps_caption_and_recheck_button(self):
        with patch.object(self.B, 'send_media', AsyncMock(side_effect=RuntimeError('offline'))):
            await self.B._show_deposit(self.bot, self.customer, 0)
        card = H.last_screen(self.bot)
        self.assertEqual(card['kind'], 'text')
        self.assertIn('YEB789', card['body'])
        self.assertEqual(card['markup'].inline_keyboard[-1][0].callback_data, 'flow:recheck')


if __name__ == '__main__':
    unittest.main()
