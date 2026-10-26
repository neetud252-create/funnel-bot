# Elena connection

Account: **@elitecsupport**. Pocket IQ bot: **@goplusaitrader_bot**.

## Current mode: activation only (2026-10-27)

`ELENA_ACTIVATION_ONLY=1` is now the default. Elena sends only the existing
activation-success confirmation and opens the customer's normal Pocket IQ
interface. The same authenticated sender checks, one-time activation version,
duplicate protection and failed-menu retries still apply. This update does not
reset accounts or change trading access requirements.

Ordinary chat replies, local fallback replies, AI, voice, old help buttons and
scheduled UID/deposit/inactivity reminders are disabled. The older verification
reminder worker is disabled too, so already-queued reminders cannot bypass this
mode. Stop and human-handoff preferences are still honored silently. No customer
messages are sent to OpenAI through Elena in this mode. The activation message
omits the OpenAI chat notice because AI conversation is disabled.

`/elenastatus` reports the current mode. `/reminderson` cannot override it.
The normal Pocket IQ menus, signals and verification flow continue to work.
To deliberately restore the earlier conversation behavior, set
`ELENA_ACTIVATION_ONLY=0` and redeploy. The sections below describe that legacy
conversation mode unless they concern activation itself.

Offline coverage: `python scripts/check_offline.py test_elena_activation_only.py
test_activation.py test_elena.py test_elena_timing.py test_verification_reminders.py`.

## Implemented

- `/start` first shows a one-time activation card with exactly one button:
  **Message Elena to Activate**. There are no Help or Language buttons on that card.
  The button opens @elitecsupport through Telegram's native `tg://resolve` chat
  target with the draft “Elena, activate my Pocket IQ account access.” There is no
  web redirect, tracker or attribution identifier. The customer must press Send
  using the same Telegram account. Other Elena contact buttons use the same
  native chat target, without inserting an activation draft.
- An authenticated incoming business message activates only that sender and
  automatically sends the existing interface in their Pocket IQ chat. Elena's
  confirmation links to @goplusaitrader_bot. Telegram does not switch the user's
  current chat automatically; the link takes them back to their ready menu.
- Contact activation is separate from broker verification. Unverified users get
  the existing welcome/free analyses/setup flow; verified users get their main
  dashboard. UID/deposit requirements, trial use and quotas remain authoritative.
- This release resets contact activation for **every account**, including verified
  customers and existing Elena contacts. Earlier activations have version 0 or 1;
  only a new authenticated incoming message records the required version 2.
  No historical contact or activation timestamp bypasses the gate. Old queued menu
  deliveries and feature buttons are blocked until the user activates again.
- New activation survives restarts and the 24-hour reply window. The required
  version is fixed in code, so a normal redeploy does not reset customers again.
  Opening the URL alone does not activate access. The one-time account reset below
  also clears previous verification and usage. Notification choices and human
  handoff records are retained; AI consent must be granted again.
  Old feature callbacks and resume links cannot bypass the gate; opt-outs and
  operator commands remain usable. A new DM is not an OpenAI consent grant.
- Elena immediately replies “Your Pocket IQ account has been activated successfully!”
  with the bot link. The confirmation and normal-bot menu are sent concurrently,
  without an intentional delay or an AI request. Telegram delivery latency still applies.
- Duplicate incoming messages cannot repeat activation. Failed normal-bot menu
  delivery retries up to three times with three-minute leases. `/start` or the
  return link always opens the current interface after contact is recorded.
- New private interactions schedule help after thirty minutes of inactivity,
  including an unsubmitted UID or confirmed incomplete qualifying deposit.
  Incorrect-UID help uses a one-minute delay. Each new interaction
  resets the pending delay; completed verification cancels the follow-up.
- No historical-user blast. Existing 24-hour reminders are suppressed after
  Elena starts helping, pauses or hands off the conversation.
- Jobs persist in PostgreSQL, recheck access and notification preferences, and
  use revision checks and one elected worker. Ambiguous sends are not repeated.
- At most two unanswered follow-ups across the existing reminder and Elena.
- Elena only manages the pinned account. Other business connections are ignored.
- Users are matched by their Telegram identity, never by a supplied Trader ID.
- Existing broker verification remains authoritative; no trade execution changes.
- Scheduled help sends directly through Elena's connected account only. No
  automatic Pocket IQ invitation is sent. Telegram requires an incoming message
  in Elena's chat within the last 24 hours; unopened/expired chats receive nothing.
  Ineligible jobs wait for chat activity without consuming a delivery attempt.
- Buttons offer relevant help, UID guidance, a real status recheck, signal/guide
  links, Later, Stop and human handoff. Stop also disables normal reminders.
- A human's outgoing message pauses automation for that customer.
- Intro/help text is translated into all 12 existing bot languages.

## All-account reset on 2026-10-25

The owner requested a fresh verification journey for every account. Before polling
or any background worker starts, `account_reset.apply` runs the fixed migration
`2026-10-25-all-accounts-v1` in one PostgreSQL transaction with an advisory lock.
It snapshots affected records in the same private database, clears active state,
and writes a completion marker. Any failure rolls back the entire transaction;
subsequent restarts skip the completed reset and preserve new progress.

- Clears every saved UID, verification result/timestamp, cached user deposit,
  Premium tier, setup/eligibility progress, daily signal usage, free trial usage,
  per-user analysis budget, fictional game tokens, gift progress and attribution.
- Clears Elena activation, conversation memory, AI/voice consent and counters;
  preserves Stop/human-handoff choices and incoming-message deduplication IDs.
- Cancels old pending broadcast deliveries and clears verification reminders.
  Every account receives a paused Elena record, preventing historical-user
  reminders from being sent merely because this reset ran.
- Retains Telegram identities, language/notification preferences, original join
  dates, existing UI/pin references, historical reporting, bot settings, media
  cache, the Elena connection and the global provider spending counter.
- Broker accounts, deposits and postback records are not modified. Users submit
  their UID again and pass the existing live campaign/deposit checks; a previously
  qualifying deposit can still qualify when checked again.

Recovery rows are in `account_reset_snapshots`, scoped by the reset ID, and counts
are in `account_reset_runs`. Conversation/reply text is deliberately excluded from
recovery records. These records never grant access and are not exposed by the bot.
Restoring them requires an explicitly requested, separately reviewed database
operation; reverting the code alone does not restore erased access state.

## Connect Telegram

1. In BotFather, enable Secretary/Business Mode for @goplusaitrader_bot.
2. Sign in as @elitecsupport. In Settings > Chat Automation, connect
   @goplusaitrader_bot. Limit it to intended customer chats and allow replies.
   It does not require profile, gifts, stars, deletion or payment permissions.
3. The bot receives `business_connection` and pins the numeric account ID.
4. Send `/elenastatus` in Pocket IQ as an administrator to inspect readiness.
5. A tester who has started Pocket IQ opens @elitecsupport and sends a message.
   Check the context buttons, Later/Stop, human takeover and language selection.

An explicit `/elena` request opens the activation card before contact, then the handoff link afterward.
`ELENA_ENABLED=0` disables Elena processing and the contact gate.
`ELENA_ACTIVATION_ENABLED=0` disables only the new gate/automatic menu handoff.
`/remindersoff` pauses scheduled deliveries; `/reminderson` resumes them.

## AI and voice status

The owner authorized OpenAI replies and optional voice on 2026-10-25. Each customer
must also press **Allow AI replies** before any conversation is sent to OpenAI.
**Use help buttons only** keeps that conversation local; **Stop** revokes consent.
Elena uses “Your Pocket IQ personal assistant” in the introduction. The activation
screen discloses automated replies before customers open Elena's chat. The success
message contains the requested confirmation, bot username and personal-assistant
introduction. The separate AI consent and voice labels remain explicit. The normal
Pocket IQ welcome after activation is unchanged.

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

`scripts/check_offline.py test_activation.py test_elena.py test_elena_ai.py test_home_menu.py test_journey.py
test_verification_reminders.py test_localization.py test_interface_language.py
test_uid_singleflight.py test_analytics.py`

These suites mock Telegram, customer data and broker verification. They do not
establish that a real Telegram account connection or live message delivery works.

`node test_elena_sql.cjs` executes the actual queue SQL in an in-memory PostgreSQL
database with synthetic users. Set `NODE_PATH` to an installed
`@electric-sql/pglite` package and `PYTHON` to Python 3. This covers claim counters,
duplicate suppression, revisions, verification, opt-out, Stop and reminder caps,
plus deferring ineligible chats without consuming attempts or sending invitations.
It also checks authenticated activation, unchanged user entitlements, durable menu
delivery leases, migration idempotency and bounded recovery after failures.
