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

    async def test_new_account_continues_directly_to_registration(self):
        card = await self.tap('setup')
        self.assertIn('Do you already have a Pocket Option account?', card['body'])
        card = await self.tap('new')
        self.assertNotIn('$', card['body'])
        self.assertIn('qualifying deposits', card['body'])
        self.assertIn('commission', card['body'])
        self.assertEqual(card['markup'].inline_keyboard[0][0].callback_data, 'flow:register')
        with patch.object(self.mod, '_ref_url', AsyncMock(return_value='https://pocketoption.com/?a=test')):
            card = await self.tap('register')
        self.assertIsNone(self.row['eligibility_confirmed_at'])
        self.assertEqual(self.row['setup_stage'], 'register')
        self.assertIn('url', card['markup'].inline_keyboard[0][0].model_dump())
        await self.tap('enter_id')
        self.assertEqual(self.state.state, self.mod.Reg.waiting_uid.state)

    async def test_continue_setup_registration_and_id_verification(self):
        self.row.update(setup_stage='requirements')
        await self.mod.home_action(H.FakeCB(91, 'home:access', 1), self.bot, self.state)
        card = H.last_screen(self.bot)
        self.assertIn('Do you already have a Pocket Option account?', card['body'])
        self.assertEqual([r[0].callback_data for r in card['markup'].inline_keyboard[:3]],
                         ['flow:existing', 'flow:new', 'flow:not_sure'])
        await self.tap('new')
        ref = 'https://pocketoption.com/?a=campaign&sub_id=source_91'
        with patch.object(self.mod, '_ref_url', AsyncMock(return_value=ref)) as referral:
            card = await self.tap('register')
        referral.assert_awaited_once_with(91)
        self.assertEqual(card['markup'].inline_keyboard[0][0].url, ref)
        self.assertEqual(card['markup'].inline_keyboard[1][0].callback_data, 'flow:enter_id')
        await self.tap('enter_id')
        self.assertEqual(self.state.state, self.mod.Reg.waiting_uid.state)
        with patch.object(self.mod, '_panel_verdict', AsyncMock(return_value=(
                config.VERIFY_NEED_DEPOSIT, Decimal('12.34')))) as lookup:
            await self.mod.capture_uid(H.FakeMessage(91, '123456789'), self.bot, self.state)
        lookup.assert_awaited_once_with(91, '123456789')
        self.assertEqual(self.row['uid'], '123456789')
        self.assertEqual(self.row['setup_stage'], 'deposit')
        self.assertFalse(self.row['verified'])
        await self.mod.home_action(H.FakeCB(91, 'home:access', 1), self.bot, self.state)
        self.assertIn('$37.66', H.last_screen(self.bot)['body'])

    async def test_verify_id_opens_immediately_and_resumes_after_restart(self):
        await self.tap('setup')
        await self.tap('enter_id')
        self.assertIsNone(self.row['eligibility_confirmed_at'])
        self.assertEqual(self.row['setup_stage'], 'id')
        self.assertEqual(self.row['account_kind'], 'existing')
        self.assertEqual(self.state.state, self.mod.Reg.waiting_uid.state)
        self.assertIn('Send your Pocket Option Trader ID', H.last_screen(self.bot)['body'])
        self.assertFalse(any(b.url for r in H.last_screen(self.bot)['markup'].inline_keyboard for b in r))
        await self.resume()
        self.assertEqual(self.state.state, self.mod.Reg.waiting_uid.state)
        self.assertIn('Send your Pocket Option Trader ID', H.last_screen(self.bot)['body'])

    async def test_typed_uid_is_checked_without_an_age_confirmation(self):
        with patch.object(self.mod, '_panel_verdict', AsyncMock(return_value=(
                config.VERIFY_NEED_DEPOSIT, Decimal('0')))) as lookup:
            await self.mod.capture_uid(H.FakeMessage(91, '123456789'), self.bot, self.state)
        lookup.assert_awaited_once_with(91, '123456789')
        self.assertIsNone(self.row['eligibility_confirmed_at'])
        self.assertEqual(self.row['uid'], '123456789')
        self.assertEqual(self.row['setup_stage'], 'deposit')
        self.assertFalse(self.row['verified'])

    async def test_invalid_uid_still_cannot_reach_the_verification_service(self):
        with patch.object(self.mod, '_panel_verdict', AsyncMock()) as lookup:
            for uid in ('12', 'not-an-id', '1234567890123456'):
                await self.mod._submit_uid(91, uid, self.bot, self.state)
            lookup.assert_not_awaited()
        self.assertIsNone(self.row['uid'])
        self.assertFalse(self.row['verified'])

    async def test_old_age_screen_and_buttons_resume_without_another_confirmation(self):
        ref = 'https://pocketoption.com/?a=test'
        for kind in ('new', 'existing'):
            for action in (None, 'eligibility', 'eligible', 'register_existing'):
                with self.subTest(kind=kind, action=action):
                    self.row.update(setup_stage='eligibility', account_kind=kind)
                    with patch.object(self.mod, '_ref_url', AsyncMock(return_value=ref)):
                        if action is None:
                            await self.mod._resume_setup(self.bot, 91, self.state)
                        else:
                            await self.tap(action)
                    self.assertEqual(H.last_screen(self.bot)['markup'].inline_keyboard[0][0].url, ref)
                    self.assertIsNone(self.row['eligibility_confirmed_at'])
        # A visitor who already chose UID entry must stay on that step.
        self.row.update(setup_stage='id', account_kind='new')
        card = await self.tap('eligible')
        self.assertIn('Send your Pocket Option Trader ID', card['body'])
        self.assertEqual(self.state.state, self.mod.Reg.waiting_uid.state)

    async def test_existing_or_unlinked_saved_account_gets_registration_card(self):
        for saved in (None, '123456789'):
            self.row.update(uid=saved, account_kind='existing')
            ref = 'https://pocketoption.com/?a=campaign&sub_id=existing_91'
            with patch.object(self.mod, '_ref_url', AsyncMock(return_value=ref)):
                card = await self.tap('new' if saved else 'existing')
            self.assertEqual(self.row['account_kind'], 'existing')
            self.assertEqual(card['kind'], 'photo')
            self.assertEqual(card['asset'], 'assets/register_account.png')
            self.assertIn('Get Pocket IQ Access', card['body'])
            self.assertEqual(card['markup'].inline_keyboard[0][0].url, ref)
            self.assertEqual(card['markup'].inline_keyboard[1][0].callback_data, 'flow:enter_id')
            self.assertEqual(self.row['uid'], saved)

    async def test_saved_id_deposit_retry_and_restart_resume(self):
        self.row.update(uid='123456789', account_kind='existing')
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
        self.assertIn('Your Pocket IQ access is ready!', self.bot.calls[-1]['body'])
        self.assertTrue(any(b.callback_data == 'menu:signal' for row in self.bot.calls[-1]['markup'].inline_keyboard for b in row))

    async def resume(self):
        self.mod = H._load_bot()
        self.state = H.FakeState()
        await self.mod.home_action(H.FakeCB(91, 'home:access', 1), self.bot, self.state)
        return H.last_screen(self.bot)

    async def test_new_path_remembers_each_question_and_registration_after_restart(self):
        await self.tap('new')
        card = await self.resume()
        self.assertIn('Ready to continue?', card['body'])
        self.assertNotIn('$', card['body'])
        self.assertFalse(any(b.url for r in card['markup'].inline_keyboard for b in r))
        ref = 'https://pocketoption.com/?a=campaign&sub_id=saved_91'
        with patch.object(self.mod, '_ref_url', AsyncMock(return_value=ref)):
            await self.tap('register')
        self.mod = H._load_bot()
        with patch.object(self.mod, '_ref_url', AsyncMock(return_value=ref)):
            await self.mod.home_action(H.FakeCB(91, 'home:access', 1), self.bot, self.state)
        card = H.last_screen(self.bot)
        self.assertEqual(card['markup'].inline_keyboard[0][0].url, ref)
        self.assertIn('I’ve Created My Account', card['markup'].inline_keyboard[1][0].text)
        await self.tap('enter_id')
        self.assertIn('What is your Pocket Option Trader ID?', (await self.resume())['body'])
        self.assertEqual(self.state.state, self.mod.Reg.waiting_uid.state)

    async def test_existing_and_unsure_paths_keep_answers_and_verify_id(self):
        await self.tap('not_sure')
        self.assertIn('Let’s help you check.', (await self.resume())['body'])
        await self.tap('existing')
        card = await self.resume()
        self.assertIn('Get Pocket IQ Access', card['body'])
        self.assertEqual(card['markup'].inline_keyboard[1][0].callback_data, 'flow:enter_id')
        await self.tap('enter_id')
        self.assertIn('Send your Pocket Option Trader ID', (await self.resume())['body'])
        with patch.object(self.mod, '_panel_verdict', AsyncMock(return_value=(
                config.VERIFY_GRANTED, Decimal('50')))) as lookup:
            await self.mod.capture_uid(H.FakeMessage(91, '123456789'), self.bot, self.state)
        lookup.assert_awaited_once_with(91, '123456789')
        card = H.last_screen(self.bot)
        self.assertEqual([r[0].callback_data for r in card['markup'].inline_keyboard],
                         ['menu:signal', 'flow:dashboard', 'flow:how'])
        await self.tap('dashboard')
        self.assertTrue(self.row['verified'])

    async def test_failed_verdict_resumes_but_corrected_id_intent_takes_priority(self):
        self.row.update(eligibility_confirmed_at='now', account_kind='existing')
        for status, expected in ((config.VERIFY_TEMPORARY, 'We cannot confirm your status yet'),
                                 (config.VERIFY_WRONG_LINK, 'We could not confirm access')):
            await self.tap('enter_id')
            self.mod._uid_lookup_at.clear()
            with patch.object(self.mod, '_panel_verdict', AsyncMock(return_value=(status, None))), \
                 patch.object(config, 'UID_VERIFY_RETRIES', 0):
                await self.mod.capture_uid(H.FakeMessage(91, '123456789'), self.bot, self.state)
            self.assertIn(expected, (await self.resume())['body'])
            await self.tap('enter_id')
            self.assertIn('What is your Pocket Option Trader ID?', (await self.resume())['body'])

    async def test_access_details_available_before_registration_without_losing_progress(self):
        await self.tap('new')
        card = await self.tap('requirements')
        self.assertIn('new Pocket Option account', card['body'])
        self.assertIn('Pocket Option UID', card['body'])
        self.assertNotIn('deposit', card['body'].lower())
        self.assertNotIn('$', card['body'])
        self.assertEqual(self.row['setup_stage'], 'requirements')
        self.assertIn('Let’s set up your account first.', (await self.resume())['body'])
        with patch.object(self.mod, '_ref_url', AsyncMock(return_value='https://pocketoption.com/?a=test')):
            card = await self.tap('register')
        self.assertNotIn('$', card['body'])
        self.assertIn('flow:requirements', [b.callback_data for r in card['markup'].inline_keyboard for b in r])

    async def test_existing_account_without_referral_has_support_and_resumable_id_check(self):
        card = await self.tap('existing')
        self.assertIn('our link below', card['body'])
        card = await self.tap('referral_help')
        self.assertIn('only one trading account', card['body'])
        self.assertEqual(card['markup'].inline_keyboard[0][0].url, journey.support_url())
        self.assertNotIn('$', card['body'])
        self.assertIn('Let’s check your referral options.', (await self.resume())['body'])
        await self.tap('enter_id')
        self.assertEqual(self.row['setup_stage'], 'id')
        self.assertEqual(self.state.state, self.mod.Reg.waiting_uid.state)

    async def test_existing_registration_opens_tracked_link_and_verifies_new_uid_directly(self):
        self.row.update(uid='111111111', account_kind='existing', access_check_status=config.VERIFY_WRONG_LINK)
        ref = 'https://pocketoption.com/?a=campaign&sub_id=existing_91'
        with patch.object(self.mod, '_ref_url', AsyncMock(return_value=ref)) as referral:
            card = await self.tap('existing')
        referral.assert_awaited_once_with(91)
        self.assertEqual(card['markup'].inline_keyboard[0][0].url, ref)
        self.assertEqual(card['asset'], 'assets/register_account.png')
        self.assertNotIn('$', card['body'])
        self.assertEqual(self.row['uid'], '111111111')
        self.assertIsNone(self.row['eligibility_confirmed_at'])
        self.mod = H._load_bot()
        with patch.object(self.mod, '_ref_url', AsyncMock(return_value=ref)):
            await self.mod.home_action(H.FakeCB(91, 'home:access', 1), self.bot, self.state)
        self.assertEqual(H.last_screen(self.bot)['markup'].inline_keyboard[0][0].url, ref)
        await self.tap('enter_id')
        with patch.object(config, 'MIN_DEPOSIT', Decimal('20')), \
             patch.object(self.mod, '_panel_verdict', AsyncMock(return_value=(
                 config.VERIFY_NEED_DEPOSIT, Decimal('0')))) as lookup:
            await self.mod.capture_uid(H.FakeMessage(91, '222222222'), self.bot, self.state)
        lookup.assert_awaited_once_with(91, '222222222')
        self.assertEqual(self.row['uid'], '222222222')
        self.assertEqual(self.row['setup_stage'], 'deposit')
        self.assertIn('$20.00 or more', H.last_screen(self.bot)['body'])

    async def test_registration_artwork_has_caption_room_and_text_fallback(self):
        import html
        import re
        import localization
        import emoji_theme
        self.assertIn((journey.REGISTER_PHOTO, 'jpg'), self.mod.referenced_assets())
        for language in localization.CODES:
            for content in (journey.EXISTING, journey.REGISTER):
                rendered = emoji_theme.html(localization.translate_message(content, language))
                plain = html.unescape(re.sub('<[^>]+>', '', rendered))
                self.assertLessEqual(len(plain.encode('utf-16-le')) // 2, 1024, language)
        with patch.object(self.mod, 'media_missing', return_value=True):
            card = await self.tap('existing')
        self.assertEqual(card['kind'], 'text')
        self.assertEqual(card['markup'].inline_keyboard[1][0].callback_data, 'flow:enter_id')

    async def test_twenty_dollar_request_only_after_confirmed_referral_and_insufficient_deposits(self):
        self.row.update(eligibility_confirmed_at='now', account_kind='new')
        await self.tap('enter_id')
        with patch.object(config, 'MIN_DEPOSIT', Decimal('20')):
            for amount, expected in ((Decimal('0'), config.VERIFY_NEED_DEPOSIT),
                                     (Decimal('20'), config.VERIFY_GRANTED)):
                self.mod._uid_lookup_at.clear()
                with patch.object(self.mod.panelbot, 'lookup_trader', AsyncMock(return_value={
                        'campaign_id': config.CAMPAIGN_ID, 'sum_deposits': amount})):
                    await self.mod.capture_uid(H.FakeMessage(91, '123456789'), self.bot, self.state)
                self.assertEqual(self.row['access_check_status'], expected)
                card = H.last_screen(self.bot)
                if expected == config.VERIFY_NEED_DEPOSIT:
                    self.assertIn('Your Trader ID is confirmed under our link.', card['body'])
                    self.assertIn('$20.00 or more', card['body'])
                    self.assertNotIn('balance is low', card['body'])
                    self.assertEqual(card['markup'].inline_keyboard[1][0].callback_data, 'flow:recheck')
                else:
                    self.assertIn('Your Pocket IQ access is ready!', card['body'])
                    self.assertTrue(self.row['verified'])

    async def test_finish_later_retains_deposit_and_existing_saved_accounts_skip_questions(self):
        self.row.update(eligibility_confirmed_at='now', account_kind='new', uid='123456789',
                        setup_stage='deposit', access_check_status=config.VERIFY_NEED_DEPOSIT,
                        access_check_deposit=Decimal('12.34'))
        await self.tap('home')
        self.assertIn('$37.66', (await self.resume())['body'])
        for action in ('setup', 'new'):
            await self.tap(action)
        self.assertIn('$37.66', H.last_screen(self.bot)['body'])
        self.assertEqual(self.row['account_kind'], 'existing')
        self.assertEqual(self.row['uid'], '123456789')

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
