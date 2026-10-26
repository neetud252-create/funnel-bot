"""Condition transitions, truthful copy and interrupted multi-bubble delivery."""
import os
import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
os.environ.setdefault('DATABASE_URL', 'postgresql://unused')
import elena as E
from test_elena import user, session, CONNECTION
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import SendMessage


class Followups(unittest.IsolatedAsyncioTestCase):
    def test_only_three_confirmed_conditions(self):
        with patch.object(E.config, 'MIN_DEPOSIT', Decimal('20')):
            self.assertEqual(E.reason_for(user(uid=None, access_check_status=None)), 'inactive')
            self.assertEqual(E.reason_for(user(access_check_status=E.config.VERIFY_INVALID_UID)), 'uid')
            self.assertIsNone(E.reason_for(user(access_check_status=E.config.VERIFY_TEMPORARY)))
            self.assertIsNone(E.reason_for(user(access_check_status=None)))
            for amount, expected in [(None,None),(Decimal('19.99'),'deposit'),(Decimal('20'),None),(Decimal('30'),None)]:
                self.assertEqual(E.reason_for(user(access_check_status=E.config.VERIFY_NEED_DEPOSIT,
                                                 access_check_deposit=amount)), expected)
        self.assertEqual([E.wait_seconds(x) for x in ('inactive','uid','deposit')], [600,300,900])

    def test_short_translated_followups_and_trusted_link(self):
        for language in E.localization.CODES:
            for reason in ('inactive','uid','deposit'):
                parts = E.messages.followup(user(language=language), reason, 'https://example.org/register?a=1&b=2', E.link())
                self.assertIn(len(parts), (2,3))
                self.assertTrue(all(len(p)<650 for p in parts))
                self.assertNotIn('95%', ''.join(parts))
                self.assertNotIn('lifetime access', ''.join(parts))
        parts = E.messages.followup(user(), 'uid', 'https://example.org/register?a=1&b=2', E.link())
        self.assertIn('a=1&amp;b=2', ''.join(parts))
        for language in E.localization.CODES[1:]:
            for source in E.messages.SOURCES:
                self.assertNotEqual(E.copy.text(source,language),source)

    def test_existing_account_and_invalid_format_do_not_push_duplicate_account(self):
        for current in [user(account_kind='existing'),user(access_check_status=E.config.VERIFY_INVALID_UID)]:
            parts=E.messages.followup(current,'uid','https://example.org/register',E.link())
            self.assertNotIn('https://example.org/register',''.join(parts))
            self.assertNotIn('old account',''.join(parts))

    def test_bubbles_keep_html_intact_and_short_greeting_single(self):
        self.assertEqual(E.messages.bubbles('Hi! How can I help you?'), ['Hi! How can I help you?'])
        link='<a href="https://example.org/?a=1&amp;b=2">Registration Link</a>'
        parts=E.messages.bubbles('First step.\n\nSecond step.\n\n'+link+'\n\nThird step.\n\nLast step.')
        self.assertLessEqual(len(parts),3)
        self.assertIn(link,'\n'.join(parts))

    async def test_stop_between_bubbles_stops_remaining_messages(self):
        job=session()
        bot=SimpleNamespace(send_message=AsyncMock())
        with patch.dict(os.environ,ELENA_ACTIVATION_ONLY='0'), \
             patch.object(E,'active_connection',AsyncMock(return_value=CONNECTION)), \
             patch.object(E.db,'get_user',AsyncMock(return_value=user())), \
             patch.object(E.store,'claim',AsyncMock(return_value=job)), \
             patch.object(E.store,'session',AsyncMock(side_effect=[job,session(stopped=True)])), \
             patch.object(E.store,'finish',AsyncMock()) as finish:
            await E.send_job(bot,object(),job)
        self.assertEqual(bot.send_message.await_count,1)
        self.assertEqual(finish.await_args.args[2],'cancelled')

    async def test_rate_limit_after_first_bubble_never_repeats_it(self):
        job=session()
        bot=SimpleNamespace(send_message=AsyncMock(side_effect=[True,TelegramRetryAfter(
            method=SendMessage(chat_id=42,text='x'),message='slow',retry_after=5)]))
        with patch.dict(os.environ,ELENA_ACTIVATION_ONLY='0'), \
             patch.object(E,'active_connection',AsyncMock(return_value=CONNECTION)), \
             patch.object(E.db,'get_user',AsyncMock(return_value=user())), \
             patch.object(E.store,'claim',AsyncMock(return_value=job)), \
             patch.object(E.store,'session',AsyncMock(return_value=job)), \
             patch.object(E.store,'finish',AsyncMock()) as finish:
            await E.send_job(bot,object(),job)
        self.assertEqual(bot.send_message.await_count,2)
        self.assertEqual(finish.await_args.args[2:4],('partial',None))

if __name__=='__main__':
    unittest.main()
