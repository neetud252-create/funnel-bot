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

Broadcasts are manual; this feature does not schedule posts, send campaigns
automatically, or copy the promotional claims in reference screenshots.
