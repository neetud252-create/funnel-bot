# Admin statistics

Send `/stats` in the bot's private chat from an existing `ADMIN_IDS` account.
The command is silent for other users and does not alter registration state.

The dashboard includes stored-user totals, new users, unique active users,
UID submissions, verified activations, gift recipients, confirmation taps,
blocked and unsubscribed users, tracked funnel stages, and the latest three
broadcasts with delivery totals and unique button clickers.

Day boundaries use Asia/Kolkata. Seven- and thirty-day windows include today
and the previous six or twenty-nine calendar days. Admins are excluded from
user and activity totals. New user counts use existing creation timestamps;
current account totals use existing database fields. Activity, repeated gift
sends, funnel visits, and campaign clicks start at analytics deployment.

The conversion cohort contains users whose first *tracked* referral confirmation
occurred in the last thirty days. Converted users have a tracked deposit
verification on that day or afterward. It is not a historical acquisition
cohort: returning accounts can enter when first tracked. Test-mode grants are
excluded. Activation totals reflect the current verified flag. No conversion
percentage is shown when the cohort is empty.

Active means an inbound private message or callback interaction, regardless of
whether a handler accepted it. A broadcast delivery alone does not count.
Registration-screen counts record opening requests. Gift-confirmation taps do
not prove a download. Telegram read receipts are not tracked. Old broadcast
buttons still work but cannot identify a campaign; new buttons carry its ID.

The analytics table stores daily aggregate event counts per user and campaign,
not message bodies or Pocket Option account IDs. Pending deliveries and
broadcast controls continue operating independently. Tracking errors are logged
and never prevent a user from completing an existing bot action.
