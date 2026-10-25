"""Model, consent and voice boundaries, entirely offline."""
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
    async def test_no_request_before_consent_or_without_key(self):
        calls = []
        transport = httpx.MockTransport(lambda request: calls.append(request))
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test'}):
            self.assertIsNone(await E.ai.reply(user(), 'hello', [], transport=transport))
            self.assertIsNone(await E.ai.speech('hello', transport=transport))
        with patch.dict(os.environ, {'OPENAI_API_KEY': ''}):
            self.assertIsNone(await E.ai.reply(user(), 'hello', [], consented=True, transport=transport))
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
                consented=True, transport=httpx.MockTransport(handler))
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
                actual = await E.ai.reply(user(), 'help', [], consented=True,
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
                    consented=True, transport=httpx.MockTransport(handler))
            self.assertEqual(result, {'reply': answer, 'action': action})
        facts = json.loads(requests[-1]['instructions'].split('Verified context:\n')[1])
        self.assertTrue(facts['registration_link_available'])
        self.assertTrue(facts['has_submitted_uid'])
        self.assertNotIn('private-attribution', json.dumps(requests))
        self.assertNotIn('https://pocketoption.com', json.dumps(requests))
        self.assertIn('first account', requests[-1]['input'][-2]['content'])

    async def test_model_failure_uses_no_raw_response(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test'}):
            actual = await E.ai.reply(user(), 'help', [], consented=True,
                transport=httpx.MockTransport(lambda request: httpx.Response(429, text='secret failed')))
        self.assertIsNone(actual)

    async def test_voice_sends_reply_only_and_validates_audio(self):
        calls = []
        def handler(request):
            calls.append((str(request.url), json.loads(request.content)))
            return httpx.Response(200, content=b'mocked-audio', headers={'content-type': 'audio/mpeg'})
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test'}):
            data = await E.ai.speech('Hello', consented=True, transport=httpx.MockTransport(handler))
        self.assertEqual(data, b'mocked-audio')
        self.assertEqual(calls[0][0], 'https://api.openai.com/v1/audio/speech')
        self.assertEqual(calls[0][1]['input'], 'Hello')
        self.assertNotIn('history', calls[0][1])


class AIFlowTests(unittest.IsolatedAsyncioTestCase):
    def message(self):
        return types.SimpleNamespace(chat=types.SimpleNamespace(type='private', id=42),
            from_user=types.SimpleNamespace(id=42), business_connection_id='business-test',
            sender_business_bot=None, via_bot=None, is_from_offline=False, message_id=7, text='help')

    async def test_first_message_requests_customer_consent_without_model_call(self):
        row = session(consent=False)
        with patch.object(E.store, 'connection', AsyncMock(return_value=CONNECTION)), \
             patch.object(E.store, 'session', AsyncMock(return_value=row)), \
             patch.object(E.store, 'incoming', AsyncMock(return_value=row)), \
             patch.object(E.db, 'get_user', AsyncMock(return_value=user())), \
             patch.object(E.ai, 'configured', return_value=True), \
             patch.object(E.ai, 'reply', AsyncMock()) as reply, \
             patch.object(E.store, 'save_question', AsyncMock()) as save, \
             patch.object(E, 'send_business', AsyncMock()) as send:
            await E.business_message(self.message(), object())
        reply.assert_not_awaited()
        self.assertEqual(send.await_args.args[2], E.copy.CONSENT)
        save.assert_awaited_once_with(42, row['revision'], 'help')

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

    async def test_hi_during_handoff_or_stop_gets_resume_without_ai_or_state_change(self):
        for flag in ('handoff', 'stopped'):
            row = session(consent=False, **{flag: True})
            message = self.message()
            message.text = 'hi'
            with self.subTest(flag=flag), \
                 patch.object(E.store, 'connection', AsyncMock(return_value=CONNECTION)), \
                 patch.object(E.store, 'session', AsyncMock(return_value=row)), \
                 patch.object(E.store, 'incoming', AsyncMock(return_value=row)), \
                 patch.object(E.db, 'get_user', AsyncMock(return_value=user())), \
                 patch.object(E.store, 'resume', AsyncMock()) as resume, \
                 patch.object(E, 'ai_answer', AsyncMock()) as answer, \
                 patch.object(E, 'send_business', AsyncMock()) as send:
                await E.business_message(message, object())
            resume.assert_not_awaited()
            answer.assert_not_awaited()
            self.assertEqual(send.await_args.args[2], E.copy.REPLIES_PAUSED)
            self.assertEqual(send.await_args.args[3].inline_keyboard[0][0].callback_data, 'elena:resume')

    async def test_resume_is_reachable_after_handoff_but_does_not_grant_ai_permission(self):
        row = session(handoff=True, consent=False)
        cb = types.SimpleNamespace(message=types.SimpleNamespace(
                chat=types.SimpleNamespace(id=42, type='private'), business_connection_id='business-test'),
            from_user=types.SimpleNamespace(id=42), data='elena:resume', answer=AsyncMock())
        with patch.object(E, 'active_connection', AsyncMock(return_value=CONNECTION)), \
             patch.object(E.store, 'session', AsyncMock(return_value=row)), \
             patch.object(E.db, 'get_user', AsyncMock(return_value=user())), \
             patch.object(E.store, 'resume', AsyncMock(return_value=session(consent=False))) as resume, \
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
        self.assertEqual(send.await_args.args[2], E.copy.CONSENT)

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
        self.assertEqual(send.await_args.args[2], E.copy.REPLIES_PAUSED)

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

    async def test_each_consented_message_reaches_ai_with_current_user_context(self):
        row = session(consent=True)
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

    async def test_question_sent_during_activation_survives_until_consent(self):
        old = session(activated_at=None, consent=False)
        activated = session(consent=False)
        message = self.message()
        message.text = 'Registration failed for 123456789'
        with patch.object(E.store, 'connection', AsyncMock(return_value=CONNECTION)), \
             patch.object(E.store, 'session', AsyncMock(return_value=old)), \
             patch.object(E.store, 'incoming', AsyncMock(return_value=old)), \
             patch.object(E.store, 'activate', AsyncMock(return_value=activated)), \
             patch.object(E.store, 'save_question', AsyncMock()) as save, \
             patch.object(E.db, 'get_user', AsyncMock(return_value=user())), \
             patch.object(E, 'confirm_activation', AsyncMock()) as confirm, \
             patch.object(E.activation, 'deliver', AsyncMock()), \
             patch.object(E.ai, 'configured', return_value=True), \
             patch.object(E, 'ai_answer', AsyncMock()) as answer, \
             patch.object(E, 'send_business', AsyncMock()) as send:
            await E.business_message(message, object())
        confirm.assert_awaited_once()
        answer.assert_not_awaited()
        self.assertNotIn('123456789', save.await_args.args[-1])
        self.assertEqual(send.await_args.args[2], E.copy.CONSENT)

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
        text, markup = send.await_args.args[2:]
        self.assertIn('Here are the registration steps.', text)
        self.assertIn('saved-source', text)
        self.assertIn(E.journey.DISCLOSURE, text)
        self.assertEqual(markup.inline_keyboard[0][0].url, url)
        self.assertEqual(markup.inline_keyboard[1][0].url, E.link('uid'))

    async def test_small_talk_reply_does_not_force_setup_menu(self):
        row = session(consent=True, history=[])
        with patch.object(E.store, 'claim_ai', AsyncMock(return_value=row)), \
             patch.object(E.store, 'session', AsyncMock(return_value=row)), \
             patch.object(E.store, 'remember', AsyncMock(return_value={'tg_id':42})), \
             patch.object(E.ai, 'reply', AsyncMock(return_value={'reply': 'You’re welcome!', 'action': 'none'})), \
             patch.object(E, 'send_business', AsyncMock()) as send:
            await E.ai_answer(object(), 42, user(), 'Thanks')
        text, markup = send.await_args.args[2:]
        self.assertEqual(text, 'You’re welcome!')
        self.assertFalse(any(b.url for row in markup.inline_keyboard for b in row))

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
        self.assertNotEqual(send.await_args.args[2], E.copy.CONSENT)

    async def test_stop_during_model_request_discards_reply(self):
        row = session(consent=True, history=[])
        with patch.object(E.store, 'claim_ai', AsyncMock(return_value=row)), \
             patch.object(E.store, 'session', AsyncMock(return_value=dict(row, stopped=True, revision=2))), \
             patch.object(E.ai, 'reply', AsyncMock(return_value={'reply': 'hello', 'action': 'uid'})), \
             patch.object(E, 'send_business', AsyncMock()) as send:
            await E.ai_answer(object(), 42, user(), 'help')
        send.assert_not_awaited()

    async def test_limited_user_gets_local_help_without_model_charge(self):
        with patch.object(E.store, 'claim_ai', AsyncMock(return_value=None)), \
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
        markup = send.await_args.args[3]
        self.assertTrue(any((b.callback_data or '').startswith('elena:voice:') for row in markup.inline_keyboard for b in row))

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
