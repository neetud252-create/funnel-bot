"""Offline Elena integration tests: no customers, credentials or live requests."""
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
os.environ.setdefault('DATABASE_URL', 'postgresql://unused')
import elena as E
import config
import localization
from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendMessage

NOW = datetime.now(timezone.utc)
CONNECTION = dict(connection_id='business-test', owner_id=99, enabled=True, can_reply=True)


def session(**kwargs):
    return dict(dict(tg_id=42, reason='uid', status='pending', revision=1, attempts=1,
        stopped=False, handoff=False, sent_count=0, business_connection_id='business-test',
        last_business_at=NOW, activated_at=NOW, activation_opened_at=NOW,
        activation_version=E.store.ACTIVATION_VERSION), **kwargs)


def user(**kwargs):
    return dict(dict(tg_id=42, language='en', verified=False, uid='123456789',
        setup_stage='help', access_check_status=config.VERIFY_WRONG_LINK), **kwargs)


class ElenaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        mode = patch.dict(os.environ, ELENA_ACTIVATION_ONLY='0')
        mode.start()
        self.addCleanup(mode.stop)
        wait = patch.object(E.ReplyTiming, 'wait', AsyncMock())
        wait.start()
        self.addCleanup(wait.stop)
        self.bot = types.SimpleNamespace(send_message=AsyncMock(), get_business_connection=AsyncMock())
        self.conn = types.SimpleNamespace(execute=AsyncMock())

    def test_classifies_actual_access_without_balances_or_new_account_assumptions(self):
        self.assertIsNone(E.reason_for(user(verified=True)))
        for status, reason in [(config.VERIFY_WRONG_LINK, 'uid'),
                               (config.VERIFY_TEMPORARY, 'temporary'),
                               (config.VERIFY_NEED_DEPOSIT, 'deposit')]:
            self.assertEqual(E.reason_for(user(access_check_status=status)), reason)
        self.assertEqual(E.reason_for(user(access_check_status=None, setup_stage='new')), 'inactive')
        self.assertEqual(E.reason_for(user(access_check_status=None, setup_stage='register')), 'registration')
        self.assertEqual(E.wait_seconds('uid'), 60)
        for reason in ('deposit', 'inactive', 'registration', 'problem', 'temporary'):
            self.assertEqual(E.wait_seconds(reason), 1800)

    async def test_bot_activity_schedules_help_without_an_elena_message(self):
        event = types.SimpleNamespace(from_user=types.SimpleNamespace(id=42, is_bot=False),
            chat=types.SimpleNamespace(id=42, type='private'), text='/start')
        cases = [
            (user(uid=None, access_check_status=None, setup_stage='new'), 'inactive', 1800),
            (user(uid=None, access_check_status=None, setup_stage='register'), 'registration', 1800),
            (user(access_check_status=config.VERIFY_NEED_DEPOSIT), 'deposit', 1800),
            (user(), 'uid', 60),
            (user(verified=True), None, 1800),
        ]
        for current, reason, delay in cases:
            with self.subTest(reason=reason), \
                 patch.object(E, 'enabled', return_value=True), \
                 patch.object(E.config, 'ADMIN_IDS', []), \
                 patch.object(E.db, 'get_user', AsyncMock(return_value=current)), \
                 patch.object(E.store, 'activity', AsyncMock()) as activity, \
                 patch.object(E.store, 'observe', AsyncMock()) as observe:
                handler = AsyncMock(return_value='normal bot response')
                result = await E.ActivityMiddleware()(handler, event, {})
                self.assertEqual(result, 'normal bot response')
                activity.assert_awaited_once_with(42)
                observe.assert_awaited_once_with(42, reason, delay)
                handler.assert_awaited_once_with(event, {})

    async def test_activated_admin_customer_flow_schedules_the_same_reminders(self):
        sender = types.SimpleNamespace(id=42, is_bot=False)
        chat = types.SimpleNamespace(id=42, type='private')
        cases = [
            (types.SimpleNamespace(from_user=sender, chat=chat, text='123456789'), user(), 'uid', 60),
            (types.SimpleNamespace(from_user=sender, chat=chat, text='/start@goplusaitrader_bot'),
             user(uid=None, access_check_status=None, setup_stage='new'), 'inactive', 1800),
            (types.SimpleNamespace(from_user=sender, message=types.SimpleNamespace(chat=chat),
                                   data='flow:check_access'),
             user(access_check_status=config.VERIFY_NEED_DEPOSIT), 'deposit', 1800),
        ]
        for event, current, reason, delay in cases:
            with self.subTest(reason=reason), \
                 patch.object(E, 'enabled', return_value=True), \
                 patch.object(E.config, 'ADMIN_IDS', [42]), \
                 patch.object(E.store, 'session', AsyncMock(return_value=session())), \
                 patch.object(E.broadcast, 'draft', AsyncMock(return_value=None)), \
                 patch.object(E.db, 'get_user', AsyncMock(return_value=current)), \
                 patch.object(E.store, 'activity', AsyncMock()) as activity, \
                 patch.object(E.store, 'observe', AsyncMock()) as observe:
                handler = AsyncMock(return_value='customer response')
                self.assertEqual(await E.ActivityMiddleware()(handler, event, {}), 'customer response')
                handler.assert_awaited_once_with(event, {})
                activity.assert_awaited_once_with(42)
                observe.assert_awaited_once_with(42, reason, delay)

    async def test_admin_operations_and_unactivated_admins_do_not_schedule(self):
        sender = types.SimpleNamespace(id=42, is_bot=False)
        chat = types.SimpleNamespace(id=42, type='private')
        def message(text):
            return types.SimpleNamespace(from_user=sender, chat=chat, text=text)
        def callback(value):
            return types.SimpleNamespace(from_user=sender, message=types.SimpleNamespace(chat=chat), data=value)
        cases = [(message(text), session(), None) for text in
                 ('/analytics', '/BROADCAST@goplusaitrader_bot', '/premium 42', '/reminderson')]
        cases += [(callback(value), session(), None) for value in
                  ('an:summary:7:0', 'bc:preview:test', 'vr:stop', 'elena:stop')]
        cases += [(message('123456789'), session(), {'id': 'draft'}),
                  (message('123456789'), session(activation_version=0), None),
                  (message('/start'), session(activated_at=None), None),
                  (message('/start'), None, None)]
        for event, saved, draft in cases:
            with self.subTest(event=event, saved=saved), \
                 patch.object(E, 'enabled', return_value=True), \
                 patch.object(E.config, 'ADMIN_IDS', [42]), \
                 patch.object(E.store, 'session', AsyncMock(return_value=saved)), \
                 patch.object(E.broadcast, 'draft', AsyncMock(return_value=draft)), \
                 patch.object(E.store, 'activity', AsyncMock()) as activity, \
                 patch.object(E.store, 'observe', AsyncMock()) as observe:
                handler = AsyncMock(return_value='handled')
                self.assertEqual(await E.ActivityMiddleware()(handler, event, {}), 'handled')
                handler.assert_awaited_once_with(event, {})
                activity.assert_not_awaited()
                observe.assert_not_awaited()

    async def test_admin_eligibility_failure_does_not_break_uid_handler(self):
        event = types.SimpleNamespace(from_user=types.SimpleNamespace(id=42, is_bot=False),
            chat=types.SimpleNamespace(id=42, type='private'), text='123456789')
        with patch.object(E, 'enabled', return_value=True), \
             patch.object(E.config, 'ADMIN_IDS', [42]), \
             patch.object(E.store, 'session', AsyncMock(side_effect=RuntimeError)), \
             patch.object(E.store, 'observe', AsyncMock()) as observe:
            handler = AsyncMock(return_value='verified')
            self.assertEqual(await E.ActivityMiddleware()(handler, event, {}), 'verified')
            observe.assert_not_awaited()

    def test_business_window_rejects_wrong_connection_expiry_and_revocation(self):
        self.assertTrue(E.business_window(session(), CONNECTION, NOW))
        self.assertFalse(E.business_window(session(last_business_at=NOW-timedelta(hours=24)), CONNECTION, NOW))
        self.assertFalse(E.business_window(session(business_connection_id='foreign'), CONNECTION, NOW))
        self.assertFalse(E.business_window(session(), dict(CONNECTION, can_reply=False), NOW))
        self.assertFalse(E.business_window(session(), dict(CONNECTION, enabled=False), NOW))
        self.assertFalse(E.business_window(session(last_business_at=None), CONNECTION, NOW))

    async def test_username_allowlist_then_numeric_owner_pin(self):
        event = types.SimpleNamespace(user=types.SimpleNamespace(id=99, username='wrong'),
                                      is_enabled=True, rights=types.SimpleNamespace(can_reply=True))
        with patch.object(E.store, 'connection', AsyncMock(return_value=None)), patch.object(E.store, 'save_connection', AsyncMock()) as save:
            await E.connection_changed(event)
            save.assert_not_awaited()
            event.user.username = E.USERNAME
            await E.connection_changed(event)
            save.assert_awaited_once_with(event)
        with patch.object(E.store, 'connection', AsyncMock(return_value=CONNECTION)), patch.object(E.store, 'save_connection', AsyncMock()) as save:
            event.user.id = 100
            await E.connection_changed(event)
            save.assert_not_awaited()

    async def test_unconnected_queue_sends_nothing(self):
        with patch.object(E, 'active_connection', AsyncMock(return_value=None)):
            await E.send_job(self.bot, self.conn, session())
        self.bot.send_message.assert_not_awaited()

    async def test_resolved_during_verification_never_receives_deposit_prompt(self):
        with patch.object(E, 'active_connection', AsyncMock(return_value=CONNECTION)), \
             patch.object(E.db, 'get_user', AsyncMock(return_value=user(access_check_status=config.VERIFY_NEED_DEPOSIT))), \
             patch.object(E, 'refresh', AsyncMock(return_value=user(verified=True))), \
             patch.object(E.store, 'claim', AsyncMock()) as claim:
            await E.send_job(self.bot, self.conn, session(reason='deposit'))
        claim.assert_not_awaited()
        self.bot.send_message.assert_not_awaited()

    async def test_state_change_during_lookup_invalidates_claim(self):
        with patch.object(E, 'active_connection', AsyncMock(return_value=CONNECTION)), \
             patch.object(E.db, 'get_user', AsyncMock(return_value=user())), \
             patch.object(E.store, 'claim', AsyncMock(return_value=None)):
            await E.send_job(self.bot, self.conn, session())
        self.bot.send_message.assert_not_awaited()

    async def test_ineligible_chats_get_no_dm_or_invitation_and_consume_no_attempt(self):
        for job in (session(last_business_at=None),
                    session(last_business_at=NOW-timedelta(hours=24)),
                    session(business_connection_id='foreign')):
            with self.subTest(job=job), \
                 patch.object(E, 'active_connection', AsyncMock(return_value=CONNECTION)), \
                 patch.object(E.db, 'get_user', AsyncMock(return_value=user())), \
                 patch.object(E.store, 'await_chat', AsyncMock()) as await_chat, \
                 patch.object(E.store, 'claim', AsyncMock()) as claim, \
                 patch.object(E.store, 'finish', AsyncMock()) as finish:
                await E.send_job(self.bot, self.conn, job)
            await_chat.assert_awaited_once_with(self.conn, job)
            claim.assert_not_awaited()
            finish.assert_not_awaited()
        self.bot.send_message.assert_not_awaited()

    async def test_eligible_chat_receives_direct_message_from_elena(self):
        job = session()
        with patch.object(E, 'active_connection', AsyncMock(return_value=CONNECTION)), \
             patch.object(E.db, 'get_user', AsyncMock(return_value=user())), \
             patch.object(E.store, 'claim', AsyncMock(return_value=job)), \
             patch.object(E.store, 'finish', AsyncMock()) as finish:
            await E.send_job(self.bot, self.conn, job)
        self.bot.send_message.assert_awaited_once_with(42, E.chat_text(user(), E.copy.CHAT_UID, 'uid'),
            parse_mode='HTML', business_connection_id='business-test')
        finish.assert_awaited_once_with(self.conn, job, 'sent')

    async def test_rejected_direct_message_never_falls_back_to_pocket_iq(self):
        self.bot.send_message.side_effect = TelegramForbiddenError(
            method=SendMessage(chat_id=42, text='x'), message='forbidden')
        job = session()
        with patch.object(E, 'active_connection', AsyncMock(return_value=CONNECTION)), \
             patch.object(E.db, 'get_user', AsyncMock(return_value=user())), \
             patch.object(E.store, 'claim', AsyncMock(return_value=job)), \
             patch.object(E.store, 'finish', AsyncMock()) as finish:
            await E.send_job(self.bot, self.conn, job)
        self.assertEqual(self.bot.send_message.await_count, 1)
        self.assertEqual(self.bot.send_message.await_args.kwargs['business_connection_id'], 'business-test')
        finish.assert_awaited_once_with(self.conn, job, 'blocked')

    async def test_eligible_chat_sends_as_elena_and_network_failure_is_not_retried(self):
        self.bot.send_message.side_effect = TelegramNetworkError(
            method=SendMessage(chat_id=42, text='x'), message='timeout')
        job = session()
        with patch.object(E, 'active_connection', AsyncMock(return_value=CONNECTION)), \
             patch.object(E.db, 'get_user', AsyncMock(return_value=user())), \
             patch.object(E.store, 'claim', AsyncMock(return_value=job)), \
             patch.object(E.store, 'finish', AsyncMock()) as finish:
            await E.send_job(self.bot, self.conn, job)
        self.assertEqual(self.bot.send_message.await_args.kwargs['business_connection_id'], 'business-test')
        self.assertEqual(self.bot.send_message.await_count, 1)
        finish.assert_awaited_once_with(self.conn, job, 'uncertain')

    async def test_retry_after_is_bounded(self):
        self.bot.send_message.side_effect = TelegramRetryAfter(
            method=SendMessage(chat_id=42, text='x'), message='slow', retry_after=30)
        job = session(attempts=5)
        with patch.object(E, 'active_connection', AsyncMock(return_value=CONNECTION)), \
             patch.object(E.db, 'get_user', AsyncMock(return_value=user())), \
             patch.object(E.store, 'claim', AsyncMock(return_value=job)), \
             patch.object(E.store, 'finish', AsyncMock()) as finish:
            await E.send_job(self.bot, self.conn, job)
        finish.assert_awaited_once_with(self.conn, job, 'failed', 31)

    async def test_business_handoff_and_echo_are_not_replied_to(self):
        m = types.SimpleNamespace(chat=types.SimpleNamespace(type='private', id=42),
            from_user=types.SimpleNamespace(id=99), business_connection_id='business-test',
            sender_business_bot=None, via_bot=None, is_from_offline=False)
        with patch.object(E.store, 'connection', AsyncMock(return_value=CONNECTION)), \
             patch.object(E.store, 'pause', AsyncMock()) as pause:
            await E.business_message(m, self.bot)
            pause.assert_awaited_once_with(42, handoff=True)
            m.sender_business_bot = object()
            pause.reset_mock()
            await E.business_message(m, self.bot)
            pause.assert_not_awaited()
        self.bot.send_message.assert_not_awaited()

    async def test_duplicate_business_update_does_not_reply(self):
        m = types.SimpleNamespace(chat=types.SimpleNamespace(type='private', id=42),
            from_user=types.SimpleNamespace(id=42), business_connection_id='business-test',
            sender_business_bot=None, via_bot=None, is_from_offline=False, message_id=7, text='help')
        with patch.object(E.store, 'connection', AsyncMock(return_value=CONNECTION)), \
             patch.object(E.db, 'get_user', AsyncMock(return_value=user())), \
             patch.object(E.store, 'session', AsyncMock(return_value=session())), \
             patch.object(E.store, 'incoming', AsyncMock(return_value=None)):
            await E.business_message(m, self.bot)
        self.bot.send_message.assert_not_awaited()

    async def test_refresh_never_updates_a_replaced_uid(self):
        with patch.object(E.db, 'get_user', AsyncMock(side_effect=[user(), user(uid='99999999')])), \
             patch.object(E, '_check', AsyncMock(return_value=(config.VERIFY_GRANTED, Decimal('50')))), \
             patch.object(E.db, 'set_verified', AsyncMock()) as verified:
            result = await E.refresh(42)
        verified.assert_not_awaited()
        self.assertEqual(result['uid'], '99999999')
        self.assertFalse(E.checking(42))

    def test_all_new_copy_is_translated_and_callbacks_fit(self):
        for lang in localization.CODES[1:]:
            for source in E.copy.SOURCES:
                self.assertNotEqual(source, E.copy.text(source, lang), (source, lang))
        for reason in ('uid', 'registration', 'inactive', 'deposit', 'problem', 'ready', 'temporary'):
            for row in E.keyboard(reason).inline_keyboard:
                for b in row:
                    if b.callback_data:
                        self.assertLessEqual(len(b.callback_data.encode()), 64)
        self.assertIn('Do not open another', E.copy.prompt(user(), 'uid'))


if __name__ == '__main__':
    unittest.main()
