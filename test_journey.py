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

    async def test_setup_and_legacy_account_answers_open_three_link_registration_card(self):
        ref = 'https://pocketoption.com/?a=test&sub_id=source_91'
        cards = []
        for action in ('setup', 'new', 'existing'):
            with patch.object(self.mod, '_ref_url', AsyncMock(return_value=ref)):
                card = await self.tap(action)
            cards.append(card)
            self.assertEqual(card['kind'], 'photo')
            self.assertEqual(card['asset'], 'assets/register_account.png')
            self.assertIn('Let’s activate your Pocket IQ access!', card['body'])
            self.assertIn('new Pocket Option account', card['body'])
            self.assertIn('<code>123456789</code>', card['body'])
            self.assertNotIn('deposit', card['body'].lower())
            rows = card['markup'].inline_keyboard
            self.assertEqual([len(row) for row in rows], [1, 1, 1])
            self.assertEqual([row[0].style for row in rows], ['success', 'primary', 'primary'])
            self.assertEqual(rows[0][0].url, ref)
            self.assertEqual(rows[1][0].url, config.SETUP_GUIDE_URL)
            self.assertEqual(rows[2][0].url, journey.support_url())
            self.assertTrue(all(row[0].callback_data is None for row in rows))
            self.assertEqual(self.state.state, self.mod.Reg.waiting_uid.state)
        self.assertEqual(cards[0]['body'], cards[1]['body'])
        self.assertEqual(cards[0]['markup'], cards[1]['markup'])
        self.assertIsNone(self.row['eligibility_confirmed_at'])
        await self.tap('enter_id')
        self.assertEqual(self.state.state, self.mod.Reg.waiting_uid.state)

    async def test_continue_setup_registration_and_id_verification(self):
        self.row.update(setup_stage='requirements')
        ref = 'https://pocketoption.com/?a=campaign&sub_id=source_91'
        with patch.object(self.mod, '_ref_url', AsyncMock(return_value=ref)) as referral:
            await self.mod.home_action(H.FakeCB(91, 'home:access', 1), self.bot, self.state)
        card = H.last_screen(self.bot)
        self.assertIn('Let’s activate your Pocket IQ access!', card['body'])
        referral.assert_awaited_once_with(91)
        self.assertEqual(card['markup'].inline_keyboard[0][0].url, ref)
        self.assertEqual(self.state.state, self.mod.Reg.waiting_uid.state)
        # Submitting from the card requires no additional button or confirmation.
        with patch.object(self.mod, '_panel_verdict', AsyncMock(return_value=(
                config.VERIFY_NEED_DEPOSIT, Decimal('12.34')))) as lookup:
            await self.mod.capture_uid(H.FakeMessage(91, '123456789'), self.bot, self.state)
        lookup.assert_awaited_once_with(91, '123456789')
        self.assertEqual(self.row['uid'], '123456789')
        self.assertEqual(self.row['setup_stage'], 'deposit')
        self.assertFalse(self.row['verified'])
        await self.mod.home_action(H.FakeCB(91, 'home:access', 1), self.bot, self.state)
        self.assertIn('$7.66', H.last_screen(self.bot)['body'])

    async def test_verify_id_opens_immediately_and_resumes_after_restart(self):
        await self.tap('setup')
        await self.tap('enter_id')
        self.assertIsNone(self.row['eligibility_confirmed_at'])
        self.assertEqual(self.row['setup_stage'], 'id')
        self.assertEqual(self.row['account_kind'], 'new')
        self.assertEqual(self.state.state, self.mod.Reg.waiting_uid.state)
        self.assertIn('Send your Pocket Option Trader ID', H.last_screen(self.bot)['body'])
        self.assertIn('Registration Link', H.last_screen(self.bot)['markup'].inline_keyboard[0][0].text)
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
            self.assertIn('Let’s activate your Pocket IQ access!', card['body'])
            self.assertEqual(card['markup'].inline_keyboard[0][0].url, ref)
            self.assertEqual(card['markup'].inline_keyboard[1][0].url, config.SETUP_GUIDE_URL)
            self.assertEqual(self.row['uid'], saved)

    async def test_saved_id_deposit_retry_and_restart_resume(self):
        self.row.update(uid='123456789', account_kind='existing')
        with patch.object(self.mod, '_panel_verdict', AsyncMock(return_value=(config.VERIFY_NEED_DEPOSIT, Decimal('12.34')))):
            card = await self.tap('recheck')
        self.assertIn('$7.66', card['body'])
        self.assertFalse(self.row['verified'])
        self.assertEqual(self.row['setup_stage'], 'deposit')
        self.assertEqual(card['markup'].inline_keyboard[0][0].url, config.BROKER_ACCOUNT_URL)
        self.mod = H._load_bot()  # No in-memory setup state is required to resume.
        await self.mod._resume_setup(self.bot, 91)
        self.assertIn('$7.66', self.bot.calls[-1]['body'])
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

    async def test_new_path_and_legacy_intro_resume_registration_after_restart(self):
        ref = 'https://pocketoption.com/?a=campaign&sub_id=saved_91'
        with patch.object(self.mod, '_ref_url', AsyncMock(return_value=ref)):
            await self.tap('new')
        self.assertEqual(self.row['setup_stage'], 'register')
        for stage in ('register', 'requirements', 'account_question'):
            self.row['setup_stage'] = stage
            self.mod = H._load_bot()
            with patch.object(self.mod, '_ref_url', AsyncMock(return_value=ref)):
                await self.mod.home_action(H.FakeCB(91, 'home:access', 1), self.bot, self.state)
            card = H.last_screen(self.bot)
            self.assertEqual(card['markup'].inline_keyboard[0][0].url, ref)
            self.assertEqual(len(card['markup'].inline_keyboard), 3)
            self.assertEqual(card['markup'].inline_keyboard[1][0].url, config.SETUP_GUIDE_URL)
            self.assertEqual(self.state.state, self.mod.Reg.waiting_uid.state)
        await self.tap('enter_id')
        self.assertIn('What is your Pocket Option Trader ID?', (await self.resume())['body'])
        self.assertEqual(self.state.state, self.mod.Reg.waiting_uid.state)

    async def test_existing_and_unsure_paths_keep_answers_and_verify_id(self):
        await self.tap('not_sure')
        self.assertIn('Let’s help you check.', (await self.resume())['body'])
        await self.tap('existing')
        card = await self.resume()
        self.assertIn('Let’s activate your Pocket IQ access!', card['body'])
        self.assertEqual(card['markup'].inline_keyboard[1][0].url, config.SETUP_GUIDE_URL)
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
                                 (config.VERIFY_WRONG_LINK, 'Let’s fix your Pocket IQ registration!')):
            await self.tap('enter_id')
            self.mod._uid_lookup_at.clear()
            with patch.object(self.mod, '_panel_verdict', AsyncMock(return_value=(status, None))), \
                 patch.object(config, 'UID_VERIFY_RETRIES', 0):
                await self.mod.capture_uid(H.FakeMessage(91, '123456789'), self.bot, self.state)
            self.assertIn(expected, (await self.resume())['body'])
            await self.tap('enter_id')
            self.assertIn('What is your Pocket Option Trader ID?', (await self.resume())['body'])

    async def test_rejected_uid_retry_and_restart_keep_registration_link(self):
        self.row.update(account_kind='new', ref_code='saved_91')
        with patch.object(config, 'REF_LINK', 'https://pocketoption.com/?a=campaign'), \
             patch.object(self.mod, '_panel_verdict', AsyncMock(return_value=(config.VERIFY_WRONG_LINK, None))):
            ref = config.ref_url('saved_91')
            await self.tap('new')
            await self.tap('enter_id')
            await self.mod.capture_uid(H.FakeMessage(91, '123456789'), self.bot, self.state)
            for card in (H.last_screen(self.bot), await self.resume(), await self.tap('enter_id')):
                self.assertIn(ref, [b.url for row in card['markup'].inline_keyboard for b in row])
            self.assertEqual(self.state.state, self.mod.Reg.waiting_uid.state)
            self.assertFalse(self.row['verified'])
            self.assertEqual(self.row['uid'], '123456789')
            # Old no-URL help buttons also reopen registration instead of looping to ID entry.
            self.assertEqual((await self.tap('register_again'))['markup'].inline_keyboard[0][0].url, ref)
            self.assertEqual((await self.resume())['markup'].inline_keyboard[0][0].url, ref)
        self.assertEqual(self.row['uid'], '123456789')
        self.assertEqual(self.row['access_check_status'], config.VERIFY_WRONG_LINK)

    async def test_unmatched_uid_shows_recovery_artwork_and_accepts_corrected_id(self):
        self.row['ref_code'] = 'recovery_91'
        for record in (None, {'campaign_id': 'another-campaign', 'sum_deposits': Decimal('100')}):
            self.mod._uid_lookup_at.clear()
            with patch.object(self.mod.panelbot, 'lookup_trader', AsyncMock(return_value=record)), \
                 patch.object(config, 'REF_LINK', 'https://pocketoption.com/?a=test'):
                ref = config.ref_url('recovery_91')
                await self.mod.capture_uid(H.FakeMessage(91, '123456789'), self.bot, self.state)
                for card in (H.last_screen(self.bot), await self.resume()):
                    self.assertEqual(card['kind'], 'photo')
                    self.assertEqual(card['asset'], 'assets/account_recovery.png')
                    self.assertIn('Your Pocket Option account wasn’t registered using my link.', card['body'])
                    rows = card['markup'].inline_keyboard
                    self.assertEqual([len(row) for row in rows], [1, 1, 1])
                    self.assertEqual([row[0].url for row in rows], [ref,
                        config.SETUP_GUIDE_URL, journey.support_url()])
                    self.assertEqual([row[0].style for row in rows], ['success', 'primary', 'primary'])
                    self.assertTrue(all(row[0].callback_data is None for row in rows))
                    self.assertIsNotNone(self.state.state)
            self.assertFalse(self.row['verified'])
        # A corrected ID can be sent straight from the recovery card after restart.
        self.mod._uid_lookup_at.clear()
        with patch.object(self.mod, '_panel_verdict', AsyncMock(return_value=(
                config.VERIFY_GRANTED, Decimal('20')))) as lookup:
            await self.mod.capture_uid(H.FakeMessage(91, '987654321'), self.bot, self.state)
        lookup.assert_awaited_once_with(91, '987654321')
        self.assertTrue(self.row['verified'])

    async def test_recovery_caption_translations_and_missing_artwork_fallback(self):
        import html
        import re
        import localization
        from xml.etree import ElementTree as ET
        self.assertIn((journey.RECOVERY_PHOTO, 'jpg'), self.mod.referenced_assets())
        for ref in ('https://pocketoption.com/?a=test&sub_id=' + 'x' * 1500,
                    'https://pocketoption.com/?a=test&source="<quoted>"',
                    'https://pocketoption.com/?a=test&sub_id=' + 'x' * 80):
            text, rows = journey.unlinked(ref=ref)
            for language in localization.CODES:
                rendered = localization.translate_message(text, language)
                anchor = ET.fromstring('<root>' + rendered + '</root>').find('a')
                self.assertEqual(anchor.get('href'), ref)
                plain = html.unescape(re.sub('<[^>]+>', '', rendered))
                self.assertLessEqual(len(plain.encode('utf-16-le')) // 2, 1024, language)
                self.assertNotIn('{ref}', rendered)
                if language != 'en':
                    self.assertNotIn('Let’s fix your', rendered)
        with patch.object(self.mod, 'media_missing', return_value=True), \
             patch.object(self.mod, '_panel_verdict', AsyncMock(return_value=(config.VERIFY_WRONG_LINK, None))):
            await self.mod.capture_uid(H.FakeMessage(91, '123456789'), self.bot, self.state)
        card = H.last_screen(self.bot)
        self.assertEqual(card['kind'], 'text')
        self.assertEqual(len(card['markup'].inline_keyboard), 3)

    async def test_access_details_available_before_registration_without_losing_progress(self):
        await self.tap('new')
        card = await self.tap('requirements')
        self.assertIn('new Pocket Option account', card['body'])
        self.assertIn('Pocket Option UID', card['body'])

        self.assertNotIn('deposit', card['body'].lower())
        self.assertNotIn('$', card['body'])
        self.assertEqual(self.row['setup_stage'], 'register')
        self.assertIn('Let’s activate your Pocket IQ access!', (await self.resume())['body'])
        with patch.object(self.mod, '_ref_url', AsyncMock(return_value='https://pocketoption.com/?a=test')):
            card = await self.tap('register')
        self.assertNotIn('$', card['body'])
        self.assertEqual(len(card['markup'].inline_keyboard), 3)

    async def test_old_registration_button_keeps_confirmed_linked_account(self):
        self.row.update(uid='123456789', setup_stage='id', access_check_status=config.VERIFY_NEED_DEPOSIT,
                        access_check_deposit=Decimal('0'))
        card = await self.tap('register_again')
        self.assertIn('Your Trader ID is confirmed under our link.', card['body'])
        self.assertEqual(self.row['uid'], '123456789')
        self.assertFalse(self.row['verified'])

    async def test_existing_account_without_referral_has_support_and_resumable_id_check(self):
        card = await self.tap('existing')
        self.assertIn('our bot link', card['body'])
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
        from xml.etree import ElementTree as ET
        for ref in ('https://pocketoption.com/?a=test&sub_id=source_91',
                    'https://pocketoption.com/?a=test&sub_id=' + 'x' * 1500,
                    'https://pocketoption.com/?a=test&source="<quoted>"'):
            content, rows = journey.registration(ref)
            anchor = ET.fromstring('<root>' + content + '</root>').find('a')
            self.assertEqual(anchor.get('href'), ref)
            self.assertEqual(rows[0][0][1], 'url:' + ref)
            for language in localization.CODES:
                rendered = emoji_theme.html(localization.translate_message(content, language))
                plain = html.unescape(re.sub('<[^>]+>', '', rendered))
                self.assertLessEqual(len(plain.encode('utf-16-le')) // 2, 1024, language)
                self.assertNotIn('{ref}', rendered)
        invalid = 'javascript:alert(1)'
        content, rows = journey.registration(invalid)
        self.assertNotIn(invalid, content)
        self.assertEqual(rows[0][0][1], 'cb:flow:link_unavailable')
        with patch.object(self.mod, 'media_missing', return_value=True):
            card = await self.tap('existing')
        self.assertEqual(card['kind'], 'text')
        self.assertEqual(card['markup'].inline_keyboard[1][0].url, config.SETUP_GUIDE_URL)

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

    async def test_registration_card_accepts_correction_and_keeps_verified_access(self):
        await self.tap('setup')
        with patch.object(self.mod, '_panel_verdict', AsyncMock()) as lookup:
            await self.mod.capture_uid(H.FakeMessage(91, 'wrong id'), self.bot, self.state)
        lookup.assert_not_awaited()
        self.assertIsNotNone(self.state.state)
        with patch.object(self.mod, '_panel_verdict', AsyncMock(return_value=(
                config.VERIFY_GRANTED, Decimal('20')))) as lookup:
            await self.mod.capture_uid(H.FakeMessage(91, '123456789'), self.bot, self.state)
        lookup.assert_awaited_once_with(91, '123456789')
        self.assertTrue(self.row['verified'])
        for action in ('setup', 'new', 'existing', 'register_again'):
            card = await self.tap(action)
            self.assertIn('Pocket IQ main menu', card['body'])
            self.assertTrue(self.row['verified'])
            self.assertIsNone(self.state.state)

    async def test_finish_later_retains_deposit_and_existing_saved_accounts_skip_questions(self):
        self.row.update(eligibility_confirmed_at='now', account_kind='new', uid='123456789',
                        setup_stage='deposit', access_check_status=config.VERIFY_NEED_DEPOSIT,
                        access_check_deposit=Decimal('12.34'))
        await self.tap('home')
        self.assertIn('$7.66', (await self.resume())['body'])
        for action in ('setup', 'new'):
            await self.tap(action)
        self.assertIn('$7.66', H.last_screen(self.bot)['body'])
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
        self.assertEqual(card['kind'], 'text')
        self.assertIn('Asset guide', card['body'])
        self.assertEqual(card['markup'].inline_keyboard[0][0].callback_data, 'market:open')

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
