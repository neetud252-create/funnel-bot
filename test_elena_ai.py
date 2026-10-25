"""Direct replies, opt-outs, legacy consent and voice boundaries, entirely offline."""
import json
import os
import types
import unittest
from unittest.mock import AsyncMock, patch
import httpx
os.environ.setdefault('DATABASE_URL', 'postgresql://unused')
import elena as E
from test_elena import user, session, CONNECTION


def response(reply='You can check your account using the button below.', action='recheck'):
    return {'status': 'completed', 'output': [{'type': 'message', 'content': [
        {'type': 'output_text', 'text': json.dumps({'reply': reply, 'action': action})}]}]}


class AIClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_request_without_caller_authorization_or_key(self):
        calls = []
        transport = httpx.MockTransport(lambda request: calls.append(request))
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test'}):
            self.assertIsNone(await E.ai.reply(user(), 'hello', [], transport=transport))
            self.assertIsNone(await E.ai.speech('hello', transport=transport))
        with patch.dict(os.environ, {'OPENAI_API_KEY': ''}):
            self.assertIsNone(await E.ai.reply(user(), 'hello', [], authorized=True, transport=transport))
        self.assertEqual(calls, [])

    async def test_request_is_bounded_redacted_and_has_no_identity_fields(self):
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=response())
        data = user(username='privatecustomer')
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test'}):
            result = await E.ai.reply(data, 'My ID 123456789, Telegram 42, @privatecustomer, email me a@b.com',
                [{'role': 'user', 'content': 'Old id 123 456 789'}] * 30,
                authorized=True, transport=httpx.MockTransport(handler))
        self.assertEqual(result['action'], 'recheck')
        payload = requests[0]
        self.assertFalse(payload['store'])
        self.assertEqual(len(payload['input']), 7)
        encoded = json.dumps(payload)
        for private in ('123456789', '123 456 789', 'privatecustomer', 'a@b.com'):
            self.assertNotIn(private, encoded)
        facts = json.loads(payload['instructions'].split('Verified context:\n')[1])
        self.assertNotIn('uid', facts)
        self.assertNotIn('tg_id', facts)
        self.assertIn('"strict": true', encoded)

    def test_credentials_are_not_forwarded(self):
        for value in ('my password is hunter2', 'OTP 123456', 'CVV: 123', 'login code 4 5 6 7 8'):
            self.assertIn('omitted', E.ai.redact(value))

    async def test_bad_model_output_fails_closed(self):
        for data in (response(action='grant_access'), response(reply='Go https://evil.example'),
                     response(reply='<b>Deposit</b>'), {'status': 'incomplete', 'output': []}):
            with patch.dict(os.environ, {'OPENAI_API_KEY': 'test'}):
                actual = await E.ai.reply(user(), 'help', [], authorized=True,
                    transport=httpx.MockTransport(lambda request: httpx.Response(200, json=data)))
            self.assertIsNone(actual)

    async def test_conversation_and_registration_answers_use_new_actions(self):
        requests = []
        for action, answer in [('none', 'You’re welcome!'), ('register',
                '1. Open Registration Link.\n2. Complete the broker’s form.\n3. Confirm your account.\n'
                '4. Copy your Trader ID from your profile.\n5. Submit it inside Pocket IQ.')]:
            def handler(request):
                requests.append(json.loads(request.content))
                return httpx.Response(200, json=response(answer, action))
            with patch.dict(os.environ, {'OPENAI_API_KEY': 'test'}), \
                 patch.object(E.config, 'REF_LINK', 'https://pocketoption.com/?a=campaign'):
                result = await E.ai.reply(user(ref_code='private-attribution'), 'How do I register?',
                    [{'role': 'assistant', 'content': 'Do you already have an account?'},
                     {'role': 'user', 'content': 'No, this is my first account.'}],
                    authorized=True, transport=httpx.MockTransport(handler))
            self.assertEqual(result, {'reply': answer, 'action': action})
        facts = json.loads(requests[-1]['instructions'].split('Verified context:\n')[1])
        self.assertTrue(facts['registration_link_available'])
        self.assertTrue(facts['has_submitted_uid'])
        self.assertNotIn('private-attribution', json.dumps(requests))
        self.assertNotIn('https://pocketoption.com', json.dumps(requests))
        self.assertIn('first account', requests[-1]['input'][-2]['content'])

    async def test_model_failure_uses_no_raw_response(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test'}):
            actual = await E.ai.reply(user(), 'help', [], authorized=True,
                transport=httpx.MockTransport(lambda request: httpx.Response(429, text='secret failed')))
        self.assertIsNone(actual)

    async def test_voice_sends_reply_only_and_validates_audio(self):
        calls = []
        def handler(request):
            calls.append((str(request.url), json.loads(request.content)))
            return httpx.Response(200, content=b'mocked-audio', headers={'content-type': 'audio/mpeg'})
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test'}):
            data = await E.ai.speech('Hello', authorized=True, transport=httpx.MockTransport(handler))
        self.assertEqual(data, b'mocked-audio')
        self.assertEqual(calls[0][0], 'https://api.openai.com/v1/audio/speech')
        self.assertEqual(calls[0][1]['input'], 'Hello')
        self.assertNotIn('history', calls[0][1])


class AIFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Timing is exercised separately with a virtual clock and real deadlines.
        wait = patch.object(E.ReplyTiming, 'wait', AsyncMock())
        wait.start()
        self.addCleanup(wait.stop)

    def message(self):
        return types.SimpleNamespace(chat=types.SimpleNamespace(type='private', id=42),
            from_user=types.SimpleNamespace(id=42), business_connection_id='business-test',
            sender_business_bot=None, via_bot=None, is_from_offline=False, message_id=7, text='help')

    async def test_greeting_then_registration_calls_ai_directly_without_approval(self):
        row = session(consent=False, consent_prompted_at=None, history=[])
        with patch.object(E.store, 'connection', AsyncMock(return_value=CONNECTION)), \
             patch.object(E.store, 'session', AsyncMock(return_value=row)), \
             patch.object(E.store, 'incoming', AsyncMock(return_value=row)), \
             patch.object(E.db, 'get_user', AsyncMock(return_value=user(account_kind='new'))), \
             patch.object(E.ai, 'configured', return_value=True), \
             patch.object(E.ai, 'reply', AsyncMock(side_effect=[
                 {'reply': 'Hi! How can I help you?', 'action': 'none'},
                 {'reply': 'Open the registration link, complete the form, then submit your Trader ID in Pocket IQ.', 'action': 'register'}])) as model, \
             patch.object(E.store, 'claim_ai', AsyncMock(return_value=row)), \
             patch.object(E.store, 'remember', AsyncMock(return_value={'tg_id':42})), \
             patch.object(E.store, 'offer_consent', AsyncMock()) as offer, \
             patch.object(E.store, 'consent', AsyncMock()) as consent, \
             patch.object(E.config, 'REF_LINK', 'https://pocketoption.com/?a=test'), \
             patch.object(E, 'send_business', AsyncMock()) as send:
            message = self.message()
            message.text = 'hi'
            await E.business_message(message, object())
            self.assertEqual(send.await_args.args[2], 'Hi! How can I help you?')
            message.text = 'how i register in the bot'
            await E.business_message(message, object())
        self.assertEqual(model.await_count, 2)
        offer.assert_not_awaited()
        consent.assert_not_awaited()  # Do not fabricate historical user approval.
        answer = send.await_args.args[2]
        self.assertIn('complete the form', answer)
        self.assertIn('https://pocketoption.com', answer)
        self.assertNotIn(E.copy.CHAT_ASK, answer)
        self.assertNotIn(E.copy.CONSENT, answer)
        self.assertLess(len(answer.split()), 85)
        self.assertTrue(all(len(call.args) == 3 for call in send.await_args_list))

    async def test_legacy_yes_answers_saved_question_but_new_yes_is_conversation(self):
        for prompted in (None, E.datetime.now(E.timezone.utc)):
            row = session(consent=False, consent_prompted_at=prompted)
            message = self.message()
            message.text = 'YES'
            with self.subTest(prompted=bool(prompted)), \
                 patch.object(E.store, 'connection', AsyncMock(return_value=CONNECTION)), \
                 patch.object(E.store, 'session', AsyncMock(return_value=row)), \
                 patch.object(E.store, 'incoming', AsyncMock(return_value=row)), \
                 patch.object(E.db, 'get_user', AsyncMock(return_value=user())), \
                 patch.object(E.ai, 'configured', return_value=True), \
                 patch.object(E.store, 'offer_consent', AsyncMock(return_value={'tg_id':42})), \
                 patch.object(E.store, 'save_question', AsyncMock()), \
                 patch.object(E.store, 'consent', AsyncMock(return_value={'tg_id':42})) as consent, \
                 patch.object(E.store, 'take_question', AsyncMock(return_value={'pending_question':'How do I register?'})), \
                 patch.object(E, 'ai_answer', AsyncMock()) as answer, \
                 patch.object(E, 'send_business', AsyncMock()):
                await E.business_message(message, object())
            if prompted:
                consent.assert_awaited_once_with(42)
                self.assertEqual(answer.await_args.args[-1], 'How do I register?')
            else:
                consent.assert_not_awaited()
                self.assertEqual(answer.await_args.args[-1], 'YES')

    async def test_typed_no_declines_ai_without_losing_basic_help(self):
        row = session(consent=False, consent_prompted_at=E.datetime.now(E.timezone.utc))
        message = self.message()
        message.text = 'NO'
        with patch.object(E.store, 'connection', AsyncMock(return_value=CONNECTION)), \
             patch.object(E.store, 'session', AsyncMock(return_value=row)), \
             patch.object(E.store, 'incoming', AsyncMock(return_value=row)), \
             patch.object(E.db, 'get_user', AsyncMock(return_value=user())), \
             patch.object(E.store, 'local_only', AsyncMock()) as local, \
             patch.object(E, 'ai_answer', AsyncMock()) as answer, \
             patch.object(E, 'send_business', AsyncMock()) as send:
            await E.business_message(message, object())
        local.assert_awaited_once_with(42)
        answer.assert_not_awaited()
        self.assertEqual(send.await_args.args[2], E.copy.CHAT_BASIC)

    async def test_voice_command_uses_latest_reply_without_approval_or_button(self):
        row = session(consent=False, reply_nonce='latest')
        message = self.message()
        message.text = '/voice'
        with patch.object(E.store, 'connection', AsyncMock(return_value=CONNECTION)), \
             patch.object(E.store, 'session', AsyncMock(return_value=row)), \
             patch.object(E.store, 'incoming', AsyncMock(return_value=row)), \
             patch.object(E.db, 'get_user', AsyncMock(return_value=user())), \
             patch.object(E, 'voice_reply', AsyncMock()) as voice:
            await E.business_message(message, object())
        self.assertEqual(voice.await_args.args[1:], (42, 'latest', 'en'))

    async def test_concurrent_stop_cannot_be_overridden_by_allow_ai(self):
        with patch.object(E.store, 'consent', AsyncMock(return_value=None)), \
             patch.object(E.store, 'take_question', AsyncMock()) as take, \
             patch.object(E, 'ai_answer', AsyncMock()) as answer, \
             patch.object(E, 'send_business', AsyncMock()) as send:
            await E.allow_ai(object(), 42, user())
        take.assert_not_awaited()
        answer.assert_not_awaited()
        send.assert_not_awaited()

    def test_existing_accounts_do_not_receive_duplicate_registration_instructions(self):
        text = E.basic_answer(user(account_kind='existing'), 'How do I register?')
        self.assertIn(E.copy.CHAT_EXISTING, text)
        self.assertNotIn('Registration Link', text)

    def test_registration_and_bot_link_questions_include_required_link_and_disclosure(self):
        with patch.object(E.config, 'REF_LINK', 'https://pocketoption.com/?a=test'):
            for value in ('How do I register?', 'Must I use your bot link?', 'Send the referral link'):
                with self.subTest(value=value):
                    text = E.basic_answer(user(account_kind='new'), value)
                    self.assertIn(E.copy.CHAT_REGISTER, text)
                    self.assertIn(E.escape(E.registration_url(user(account_kind='new')), quote=True), text)
                    self.assertIn(E.journey.DISCLOSURE, text)
                    self.assertIn('Registration alone does not unlock full access.', text)

    def test_existing_account_explanation_keeps_link_requirement_without_duplicate_signup(self):
        for current, value in ((user(account_kind='existing'), 'Must I use the bot link?'),
                               (user(account_kind='new'), 'I already have an account')):
            with self.subTest(value=value):
                text = E.basic_answer(current, value)
                self.assertIn(E.copy.CHAT_EXISTING, text)
                self.assertIn('contact Pocket Option support', text)
                self.assertNotIn('Registration Link', text)
        self.assertEqual(E.basic_answer(user(account_kind='existing'), 'hi'), E.copy.HELLO)

    async def test_first_message_goes_directly_to_ai_without_permission_prompt(self):
        row = session(consent=False)
        with patch.object(E.store, 'connection', AsyncMock(return_value=CONNECTION)), \
             patch.object(E.store, 'session', AsyncMock(return_value=row)), \
             patch.object(E.store, 'incoming', AsyncMock(return_value=row)), \
             patch.object(E.db, 'get_user', AsyncMock(return_value=user())), \
             patch.object(E.ai, 'configured', return_value=True), \
             patch.object(E, 'ai_answer', AsyncMock()) as reply, \
             patch.object(E.store, 'offer_consent', AsyncMock()) as offer, \
             patch.object(E.store, 'save_question', AsyncMock()) as save, \
             patch.object(E, 'send_business', AsyncMock()) as send:
            await E.business_message(self.message(), object())
        self.assertEqual(reply.await_args.args[-1], 'help')
        offer.assert_not_awaited()
        save.assert_not_awaited()
        send.assert_not_awaited()

    async def test_allow_ai_answers_saved_question_without_retyping(self):
        row = session(consent=False)
        cb = types.SimpleNamespace(message=types.SimpleNamespace(
                chat=types.SimpleNamespace(id=42, type='private'), business_connection_id='business-test'),
            from_user=types.SimpleNamespace(id=42), data='elena:consent', answer=AsyncMock())
        with patch.object(E, 'active_connection', AsyncMock(return_value=CONNECTION)), \
             patch.object(E.store, 'session', AsyncMock(return_value=row)), \
             patch.object(E.db, 'get_user', AsyncMock(return_value=user())), \
             patch.object(E.store, 'pause', AsyncMock()), \
             patch.object(E.store, 'consent', AsyncMock()) as consent, \
             patch.object(E.store, 'take_question', AsyncMock(return_value={'pending_question': 'Registration fails.'})), \
             patch.object(E.ai, 'configured', return_value=True), \
             patch.object(E, 'ai_answer', AsyncMock()) as answer:
            await E.callback(cb, object())
        consent.assert_awaited_once_with(42)
        self.assertEqual(answer.await_args.args[-1], 'Registration fails.')

    async def test_new_question_reopens_paused_chat_and_answers_without_resume_or_approval(self):
        for flag in ('handoff', 'stopped'):
            for value in ('hi', 'How do I register?'):
                row = session(consent=False, **{flag: True})
                current = user()
                message = self.message()
                message.text = value
                with self.subTest(flag=flag, value=value), \
                     patch.object(E.store, 'connection', AsyncMock(return_value=CONNECTION)), \
                     patch.object(E.store, 'session', AsyncMock(return_value=row)), \
                     patch.object(E.store, 'incoming', AsyncMock(return_value=row)), \
                     patch.object(E.db, 'get_user', AsyncMock(return_value=current)), \
                     patch.object(E.store, 'resume', AsyncMock(return_value=session(consent=False, revision=2))) as resume, \
                     patch.object(E.store, 'consent', AsyncMock()) as consent, \
                     patch.object(E.store, 'offer_consent', AsyncMock()) as offer, \
                     patch.object(E.db, 'set_notifications', AsyncMock()) as notify, \
                     patch.object(E.ai, 'configured', return_value=True), \
                     patch.object(E, 'ai_answer', AsyncMock()) as answer, \
                     patch.object(E, 'send_business', AsyncMock()) as send:
                    await E.business_message(message, object())
                resume.assert_awaited_once_with(42, row['revision'])
                self.assertEqual(answer.await_args.args[-2:], (current, value))
                consent.assert_not_awaited()
                offer.assert_not_awaited()
                notify.assert_not_awaited()
                send.assert_not_awaited()

    async def test_reopened_local_only_chat_answers_without_enabling_ai(self):
        row = session(stopped=True, local_only=True)
        message = self.message()
        message.text = 'hi'
        with patch.object(E.store, 'connection', AsyncMock(return_value=CONNECTION)), \
             patch.object(E.store, 'session', AsyncMock(return_value=row)), \
             patch.object(E.store, 'incoming', AsyncMock(return_value=row)), \
             patch.object(E.db, 'get_user', AsyncMock(return_value=user())), \
             patch.object(E.store, 'resume', AsyncMock(return_value=session(local_only=True, revision=2))), \
             patch.object(E, 'ai_answer', AsyncMock()) as answer, \
             patch.object(E, 'send_chat_reply', AsyncMock()) as send:
            await E.business_message(message, object())
        answer.assert_not_awaited()
        self.assertEqual(send.await_args.args[2], E.copy.HELLO)
        self.assertEqual(send.await_args.kwargs['revision'], 2)

    async def test_new_stop_or_operator_event_wins_over_automatic_reopening(self):
        row = session(handoff=True)
        with patch.object(E.store, 'connection', AsyncMock(return_value=CONNECTION)), \
             patch.object(E.store, 'session', AsyncMock(return_value=row)), \
             patch.object(E.store, 'incoming', AsyncMock(return_value=row)), \
             patch.object(E.db, 'get_user', AsyncMock(return_value=user())), \
             patch.object(E.store, 'resume', AsyncMock(return_value=None)) as resume, \
             patch.object(E, 'ai_answer', AsyncMock()) as answer, \
             patch.object(E, 'send_business', AsyncMock()) as send:
            await E.business_message(self.message(), object())
        resume.assert_awaited_once_with(42, row['revision'])
        answer.assert_not_awaited()
        send.assert_not_awaited()

    async def test_stop_message_is_acknowledged_without_reopening_or_resume_instructions(self):
        message = self.message()
        message.text = 'stop'
        with patch.object(E.store, 'connection', AsyncMock(return_value=CONNECTION)), \
             patch.object(E.db, 'get_user', AsyncMock(return_value=user())), \
             patch.object(E.store, 'pause', AsyncMock()) as pause, \
             patch.object(E.store, 'resume', AsyncMock()) as resume, \
             patch.object(E.db, 'set_notifications', AsyncMock()) as notify, \
             patch.object(E, 'ai_answer', AsyncMock()) as answer, \
             patch.object(E, 'send_business', AsyncMock()) as send:
            await E.business_message(message, object())
        pause.assert_awaited_once_with(42, stopped=True, handoff=False)
        notify.assert_awaited_once_with(42, False)
        resume.assert_not_awaited()
        answer.assert_not_awaited()
        self.assertNotIn('/resume', send.await_args.args[2])
        self.assertIn(E.copy.REOPEN, send.await_args.args[2])

    async def test_resume_returns_greeting_without_permission_step_or_fabricated_consent(self):
        row = session(handoff=True, consent=False)
        cb = types.SimpleNamespace(message=types.SimpleNamespace(
                chat=types.SimpleNamespace(id=42, type='private'), business_connection_id='business-test'),
            from_user=types.SimpleNamespace(id=42), data='elena:resume', answer=AsyncMock())
        with patch.object(E, 'active_connection', AsyncMock(return_value=CONNECTION)), \
             patch.object(E.store, 'session', AsyncMock(return_value=row)), \
             patch.object(E.db, 'get_user', AsyncMock(return_value=user())), \
             patch.object(E.store, 'resume', AsyncMock(return_value=session(consent=False))) as resume, \
             patch.object(E.store, 'offer_consent', AsyncMock(return_value={'tg_id':42})), \
             patch.object(E.store, 'consent', AsyncMock()) as consent, \
             patch.object(E.db, 'set_notifications', AsyncMock()) as notify, \
             patch.object(E.ai, 'configured', return_value=True), \
             patch.object(E, 'ai_answer', AsyncMock()) as answer, \
             patch.object(E, 'send_business', AsyncMock()) as send:
            await E.callback(cb, object())
        resume.assert_awaited_once_with(42, row['revision'])
        consent.assert_not_awaited()
        notify.assert_not_awaited()
        answer.assert_not_awaited()
        self.assertEqual(send.await_args.args[2], E.copy.HELLO)

    async def test_old_consent_button_cannot_override_human_takeover(self):
        row = session(handoff=True, consent=False)
        cb = types.SimpleNamespace(message=types.SimpleNamespace(
                chat=types.SimpleNamespace(id=42, type='private'), business_connection_id='business-test'),
            from_user=types.SimpleNamespace(id=42), data='elena:consent', answer=AsyncMock())
        with patch.object(E, 'active_connection', AsyncMock(return_value=CONNECTION)), \
             patch.object(E.store, 'session', AsyncMock(return_value=row)), \
             patch.object(E.db, 'get_user', AsyncMock(return_value=user())), \
             patch.object(E.store, 'resume', AsyncMock()) as resume, \
             patch.object(E.store, 'consent', AsyncMock()) as consent, \
             patch.object(E, 'send_business', AsyncMock()) as send:
            await E.callback(cb, object())
        resume.assert_not_awaited()
        consent.assert_not_awaited()
        self.assertEqual(send.await_args.args[2], E.copy.CHAT_PAUSED)

    async def test_new_stop_wins_over_a_concurrent_resume(self):
        row = session(handoff=True, consent=True)
        cb = types.SimpleNamespace(message=types.SimpleNamespace(
                chat=types.SimpleNamespace(id=42, type='private'), business_connection_id='business-test'),
            from_user=types.SimpleNamespace(id=42), data='elena:resume', answer=AsyncMock())
        with patch.object(E, 'active_connection', AsyncMock(return_value=CONNECTION)), \
             patch.object(E.store, 'session', AsyncMock(return_value=row)), \
             patch.object(E.db, 'get_user', AsyncMock(return_value=user())), \
             patch.object(E.store, 'resume', AsyncMock(return_value=None)), \
             patch.object(E, 'send_business', AsyncMock()) as send:
            await E.callback(cb, object())
        send.assert_not_awaited()

    async def test_each_message_reaches_ai_with_current_user_context_without_approval(self):
        row = session(consent=False)
        current = user(language='hi')
        with patch.object(E.store, 'connection', AsyncMock(return_value=CONNECTION)), \
             patch.object(E.store, 'session', AsyncMock(return_value=row)), \
             patch.object(E.store, 'incoming', AsyncMock(return_value=row)), \
             patch.object(E.db, 'get_user', AsyncMock(return_value=current)), \
             patch.object(E.ai, 'configured', return_value=True), \
             patch.object(E, 'ai_answer', AsyncMock()) as answer:
            for value in ('Hello', 'I cannot register', 'Where do I find the ID?', 'Thank you'):
                message = self.message()
                message.text = value
                await E.business_message(message, object())
                self.assertEqual(answer.await_args.args[-2:], (current, value))
        self.assertEqual(answer.await_count, 4)

    async def test_question_during_activation_is_answered_immediately(self):
        old = session(activated_at=None, consent=False)
        activated = session(consent=False)
        message = self.message()
        message.text = 'Registration failed for 123456789'
        with patch.object(E.store, 'connection', AsyncMock(return_value=CONNECTION)), \
             patch.object(E.store, 'session', AsyncMock(return_value=old)), \
             patch.object(E.store, 'incoming', AsyncMock(return_value=old)), \
             patch.object(E.store, 'activate', AsyncMock(return_value=activated)), \
             patch.object(E.store, 'offer_consent', AsyncMock(return_value={'tg_id':42})), \
             patch.object(E.store, 'save_question', AsyncMock()) as save, \
             patch.object(E.db, 'get_user', AsyncMock(return_value=user())), \
             patch.object(E, 'confirm_activation', AsyncMock()) as confirm, \
             patch.object(E.activation, 'deliver', AsyncMock()), \
             patch.object(E.ai, 'configured', return_value=True), \
             patch.object(E, 'ai_answer', AsyncMock()) as answer, \
             patch.object(E, 'send_business', AsyncMock()) as send:
            await E.business_message(message, object())
        confirm.assert_awaited_once()
        self.assertEqual(answer.await_args.args[-1], message.text)
        save.assert_not_awaited()
        send.assert_not_awaited()

    async def test_registration_reply_includes_authoritative_link_and_next_step(self):
        row = session(consent=True, history=[])
        data = user(ref_code='saved-source')
        with patch.object(E.config, 'REF_LINK', 'https://pocketoption.com/?a=campaign'), \
             patch.object(E.store, 'claim_ai', AsyncMock(return_value=row)), \
             patch.object(E.store, 'session', AsyncMock(return_value=row)), \
             patch.object(E.store, 'remember', AsyncMock(return_value={'tg_id':42})), \
             patch.object(E.ai, 'reply', AsyncMock(return_value={'reply': 'Here are the registration steps.', 'action': 'register'})), \
             patch.object(E, 'send_business', AsyncMock()) as send:
            await E.ai_answer(object(), 42, data, 'Send the registration link')
            url = E.registration_url(data)
        text = send.await_args.args[2]
        self.assertIn('Here are the registration steps.', text)
        self.assertIn('saved-source', text)
        self.assertIn(E.journey.DISCLOSURE, text)
        self.assertIn(E.escape(url, quote=True), text)
        self.assertIn(E.link('uid'), text)
        self.assertEqual(len(send.await_args.args), 3)

    async def test_small_talk_reply_does_not_force_setup_menu(self):
        row = session(consent=True, history=[])
        with patch.object(E.store, 'claim_ai', AsyncMock(return_value=row)), \
             patch.object(E.store, 'session', AsyncMock(return_value=row)), \
             patch.object(E.store, 'remember', AsyncMock(return_value={'tg_id':42})), \
             patch.object(E.ai, 'reply', AsyncMock(return_value={'reply': 'You’re welcome!', 'action': 'none'})), \
             patch.object(E, 'send_business', AsyncMock()) as send:
            await E.ai_answer(object(), 42, user(), 'Thanks')
        text = send.await_args.args[2]
        self.assertEqual(text, 'You’re welcome!')
        self.assertEqual(len(send.await_args.args), 3)

    async def test_help_only_choice_does_not_keep_asking_for_ai(self):
        row = session(consent=False, local_only=True)
        with patch.object(E.store, 'connection', AsyncMock(return_value=CONNECTION)), \
             patch.object(E.store, 'session', AsyncMock(return_value=row)), \
             patch.object(E.store, 'incoming', AsyncMock(return_value=row)), \
             patch.object(E.db, 'get_user', AsyncMock(return_value=user())), \
             patch.object(E.ai, 'configured', return_value=True), \
             patch.object(E.ai, 'reply', AsyncMock()) as reply, \
             patch.object(E, 'send_business', AsyncMock()) as send:
            await E.business_message(self.message(), object())
        reply.assert_not_awaited()
        self.assertNotIn(E.copy.CHAT_ASK, send.await_args.args[2])

    async def test_stop_during_model_request_discards_reply(self):
        row = session(consent=True, history=[])
        with patch.object(E.store, 'claim_ai', AsyncMock(return_value=row)), \
             patch.object(E.store, 'session', AsyncMock(return_value=dict(row, stopped=True, revision=2))), \
             patch.object(E.ai, 'reply', AsyncMock(return_value={'reply': 'hello', 'action': 'uid'})), \
             patch.object(E, 'send_business', AsyncMock()) as send:
            await E.ai_answer(object(), 42, user(), 'help')
        send.assert_not_awaited()

    async def test_basic_command_opts_out_without_a_confirmation_step(self):
        row = session(consent=False)
        message = self.message()
        message.text = '/basic'
        with patch.object(E.store, 'connection', AsyncMock(return_value=CONNECTION)), \
             patch.object(E.store, 'session', AsyncMock(return_value=row)), \
             patch.object(E.store, 'incoming', AsyncMock(return_value=row)), \
             patch.object(E.db, 'get_user', AsyncMock(return_value=user())), \
             patch.object(E.store, 'local_only', AsyncMock()) as local, \
             patch.object(E, 'ai_answer', AsyncMock()) as answer, \
             patch.object(E, 'send_business', AsyncMock()) as send:
            await E.business_message(message, object())
        local.assert_awaited_once_with(42)
        answer.assert_not_awaited()
        self.assertEqual(send.await_args.args[2], E.copy.CHAT_BASIC)

    async def test_local_only_during_model_request_discards_reply(self):
        row = session(consent=False, history=[])
        with patch.object(E.store, 'claim_ai', AsyncMock(return_value=row)), \
             patch.object(E.store, 'session', AsyncMock(return_value=dict(row, local_only=True))), \
             patch.object(E.ai, 'reply', AsyncMock(return_value={'reply': 'hello', 'action': 'none'})), \
             patch.object(E, 'send_business', AsyncMock()) as send:
            await E.ai_answer(object(), 42, user(), 'help')
        send.assert_not_awaited()

    async def test_limited_user_gets_local_help_without_model_charge(self):
        with patch.object(E.store, 'claim_ai', AsyncMock(return_value=None)), \
             patch.object(E.store, 'session', AsyncMock(return_value=session())), \
             patch.object(E.ai, 'reply', AsyncMock()) as reply, \
             patch.object(E, 'send_business', AsyncMock()) as send:
            await E.ai_answer(object(), 42, user(), 'help')
        reply.assert_not_awaited()
        send.assert_awaited_once()

    async def test_reply_has_optional_voice_not_automatic_voice(self):
        row = session(consent=True, history=[])
        with patch.object(E.store, 'claim_ai', AsyncMock(return_value=row)), \
             patch.object(E.store, 'session', AsyncMock(return_value=row)), \
             patch.object(E.store, 'remember', AsyncMock(return_value={'tg_id': 42})), \
             patch.object(E.ai, 'reply', AsyncMock(return_value={'reply': 'Please check your ID.', 'action': 'uid'})), \
             patch.object(E.ai, 'speech', AsyncMock()) as speech, \
             patch.object(E, 'send_business', AsyncMock()) as send:
            await E.ai_answer(object(), 42, user(), 'help')
        speech.assert_not_awaited()
        self.assertEqual(len(send.await_args.args), 3, 'Voice is requested with /voice, never a routine button')

    async def test_duplicate_voice_or_old_reply_never_calls_provider(self):
        with patch.object(E.store, 'claim_voice', AsyncMock(return_value=None)), \
             patch.object(E.ai, 'speech', AsyncMock()) as speech:
            await E.voice_reply(object(), 42, 'oldnonce', 'en')
        speech.assert_not_awaited()

    async def test_human_takeover_during_voice_discards_audio(self):
        row = session(consent=True, reply_text='hello', reply_nonce='n')
        bot = types.SimpleNamespace(send_voice=AsyncMock())
        with patch.object(E.store, 'claim_voice', AsyncMock(return_value=row)), \
             patch.object(E.store, 'session', AsyncMock(return_value=dict(row, handoff=True))), \
             patch.object(E, 'active_connection', AsyncMock(return_value=CONNECTION)), \
             patch.object(E.ai, 'speech', AsyncMock(return_value=b'audio')):
            await E.voice_reply(bot, 42, 'n', 'en')
        bot.send_voice.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
