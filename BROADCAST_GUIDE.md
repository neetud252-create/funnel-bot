# Go+ broadcasts

Only Telegram IDs listed in the existing `ADMIN_IDS` configuration can draft or
send broadcasts. Use these commands in the bot's private chat:

1. `/broadcast` — starts a draft.
2. Send one text message, photo, video, round video, animation, or document.
   Captions and Telegram text formatting are copied. Albums are not supported.
3. Select the audience and an optional button.
4. Tap **Preview** to receive a copy and see the eligible audience count.
5. Tap **Send to … users** to confirm delivery. A changed draft needs a new preview.

The registration button opens the existing registration screen for unverified
users; verified users open their menu. The Open Go+ button uses the same account
check, so it cannot bypass verification. Registration progress is untouched by
receiving a broadcast.

Audiences are based on current database state: all users, users with no submitted
UID, users with a submitted UID but no verified activation, and activated users.
A submitted UID alone is not proof of referral registration.

`/cancelbroadcast` cancels an unsent draft. `/broadcaststatus` reports the latest
broadcast's delivered, blocked, failed, uncertain, skipped, and remaining counts.
An automatic report is sent at completion. Do not delete the source post until
delivery completes, because the bot copies it from Telegram.

Only admins see the **Stop broadcast messages** button. Recipients can send `/unsubscribe`.
`/subscribe` enables broadcasts again. These commands do not affect bot access.
Blocked and unsubscribed users are excluded. Round videos receive their buttons
in a separate message because video notes cannot carry inline keyboards.

Queued recipients are stored in PostgreSQL and pending deliveries resume after
restarts. A database advisory lock permits one broadcast worker. Sends are paced
and Telegram rate-limit responses are honored. Interrupted or ambiguous sends
are reported as uncertain and are not automatically repeated: Telegram and the
database cannot provide an atomic exactly-once acknowledgement.

Broadcast posts remain manual. Verification reminders are a separate automation.

## Automatic verification reminders

Enabled on first deployment. Each existing or future private-chat user who is
still unverified at least 24 hours after joining receives one neutral reminder
to open verification and check their Pocket Option Trader ID. Both users without
a UID and users with a submitted UID are included. Unverified does not mean the
submitted ID is necessarily incorrect.

Admins, verified users, unsubscribed users and blocked users are excluded. The
eligibility check runs again before each send. Receiving a reminder does not
change registration state or verification. **Open verification** uses the existing
access-checked route. **Stop reminders** and `/unsubscribe` stop reminders and
broadcasts while preserving bot access; `/subscribe` restores eligibility but
does not reset a previously sent reminder.

Admin commands, in a private chat with Go+:
- `/verificationreminders`: status, eligible count, delivery totals and message text.
- `/remindersoff`: pause automatic verification reminders.
- `/reminderson`: resume; does not resend completed reminders.

The worker checks every minute, sends at most about one reminder per second and
obeys Telegram retry-after responses (up to five explicit rate-limit retries).
The PostgreSQL delivery record survives redeploys; a single-worker advisory lock
and atomic per-user claim prevent duplicate jobs. Interrupted or ambiguous sends
are marked **uncertain** and not automatically repeated. Delivery is therefore
best effort, not a guarantee that every eligible user receives or reads a message.
Pausing persists across redeploys. An already in-flight message may still finish
when a user verifies, opts out, or an admin pauses.

