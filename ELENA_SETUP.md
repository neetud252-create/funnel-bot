# Elena connection

Account: **@elitecsupport**. Pocket IQ bot: **@goplusaitrader_bot**.

## Implemented

- Existing `/start` welcome and verified dashboard are preserved.
- New private interactions schedule help after ten minutes of inactivity,
  including an unsubmitted UID or confirmed incomplete qualifying deposit.
  Incorrect-UID help retains the agreed five-minute delay. Each new interaction
  resets the pending delay; completed verification cancels the follow-up.
- No historical-user blast. Existing 24-hour reminders are suppressed after
  Elena starts helping, pauses or hands off the conversation.
- Jobs persist in PostgreSQL, recheck access and notification preferences, and
  use revision checks and one elected worker. Ambiguous sends are not repeated.
- At most two unanswered follow-ups across the existing reminder and Elena.
- Elena only manages the pinned account. Other business connections are ignored.
- Users are matched by their Telegram identity, never by a supplied Trader ID.
- Existing broker verification remains authoritative; no trade execution changes.
- Unopened/expired Elena chats receive a help invitation inside Pocket IQ. Elena
  can reply on behalf of the account only within Telegram's permitted window.
- Buttons offer relevant help, UID guidance, a real status recheck, signal/guide
  links, Later, Stop and human handoff. Stop also disables normal reminders.
- A human's outgoing message pauses automation for that customer.
- Intro/help text is translated into all 12 existing bot languages.

## Connect Telegram

1. In BotFather, enable Secretary/Business Mode for @goplusaitrader_bot.
2. Sign in as @elitecsupport. In Settings > Chat Automation, connect
   @goplusaitrader_bot. Limit it to intended customer chats and allow replies.
   It does not require profile, gifts, stars, deletion or payment permissions.
3. The bot receives `business_connection` and pins the numeric account ID.
4. Send `/elenastatus` in Pocket IQ as an administrator to inspect readiness.
5. A tester who has started Pocket IQ opens @elitecsupport and sends a message.
   Check the context buttons, Later/Stop, human takeover and language selection.

`/elena` opens the handoff link. `ELENA_ENABLED=0` disables Elena processing.
`/remindersoff` pauses scheduled deliveries; `/reminderson` resumes them.

## AI and voice status

The owner authorized OpenAI replies and optional voice on 2026-10-25. Each customer
must also press **Allow AI replies** before any conversation is sent to OpenAI.
**Use help buttons only** keeps that conversation local; **Stop** revokes consent.
Elena identifies herself as an AI assistant. The normal Pocket IQ welcome is unchanged.

OpenAI receives at most six redacted history messages plus the current message,
language, setup stage, verification result and confirmed qualifying-deposit amount.
Known Telegram/Trader IDs, usernames, URLs, emails and long digit strings are
redacted. Messages mentioning credentials are omitted. Redaction is conservative
but cannot recognize every possible secret, so users are told not to share them.
Only the last eight redacted messages are kept locally, cleared at least every
24 hours (on the next 30-second cleanup pass), and immediately on Stop/human handoff. Responses use
`store: false`; this does not imply OpenAI has no abuse-monitoring retention.

The reply action is constrained to setup, UID help, recheck, signals help,
support, later or stop. AI cannot grant access, alter verification, make payments
or trade. Recheck always runs the existing broker verification rules. Status
and deposit claims from customers are not treated as verified facts.

**Listen · AI voice via OpenAI** sends only the displayed reply text to OpenAI.
The voice note is labeled AI-generated; the text remains visible. Voice never
runs automatically. Persisted limits are 40 AI attempts and five voice attempts
per customer per UTC day, a five-second reply cooldown, and one voice attempt
per displayed response. Each provider request has a 25-second overall deadline.
On failure, existing translated help buttons remain available.

Configuration: `OPENAI_API_KEY` (already used by Pocket IQ), optional `ELENA_MODEL`
(default `gpt-6-luna`), `ELENA_VOICE_MODEL` (default `gpt-4o-mini-tts`) and
`ELENA_VOICE` (default `coral`). OpenAI API usage is metered; this does not add a
market-data subscription. The current TTS endpoint/model is scheduled for retirement
on 2027-01-06 according to OpenAI's documentation; migrate the voice provider before
then. If voice fails, text help continues.

## References

- https://core.telegram.org/bots/features#business-bots
- https://core.telegram.org/bots/api#businessbotrights
- https://telegram.org/blog/ai-bot-revolution-11-new-features#chat-automation-in-profiles
- https://developers.openai.com/api/docs/guides/structured-outputs
- https://developers.openai.com/api/docs/guides/text-to-speech

## Offline validation

`scripts/check_offline.py test_elena.py test_elena_ai.py test_home_menu.py test_journey.py
test_verification_reminders.py test_localization.py test_interface_language.py
test_uid_singleflight.py test_analytics.py`

These suites mock Telegram, customer data and broker verification. They do not
establish that a real Telegram account connection or live message delivery works.

`node test_elena_sql.cjs` executes the actual queue SQL in an in-memory PostgreSQL
database with synthetic users. Set `NODE_PATH` to an installed
`@electric-sql/pglite` package and `PYTHON` to Python 3. This covers claim counters,
duplicate suppression, revisions, verification, opt-out, Stop and reminder caps.
