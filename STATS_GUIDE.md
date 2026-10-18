# Admin statistics

## Detailed analytics

Send `/analytics` in a **private chat** with the bot using an account listed in
`ADMIN_IDS`. Use the buttons to choose Today, 7, 30 or 90 calendar days, or send
`/analytics 7`. Reports and CSV files are unavailable to other accounts and to
group chats. Viewing reports does not change the admin's registration state.

The report menu includes:

- **Overview:** all stored users; new, active and returning users; native button
  taps; tracked link opens; verifications; errors; current account totals.
- **Top buttons:** per-screen taps/opens, unique users, users shown the button,
  and engagement among people shown that same button within the selected period.
  Taps on older messages count in totals, but not in the new-exposure denominator.
- **Conversion funnel:** joined → registration screen delivered → UID submitted
  → referral confirmed → deposit verified. A stage counts only after its preceding
  stage. The cohort includes users who joined in the chosen period **after detailed
  analytics started**, so existing accounts are not invented historical conversions.
  Repeat actions do not inflate unique-user conversion counts. The report separates
  pending users from users inactive for 24 hours and shows average time to the next step.
- **Inactive / drop-offs:** last delivered screen of users with no inbound bot
  interaction for 24 hours, split into verified and unverified users. Only users
  who interacted during the selected period appear. This is an inactivity measure;
  it cannot establish a user's reason for leaving or permanent abandonment.
- **Daily activity:** joins, active users, taps, link opens and verifications,
  including days with zero activity.
- **Sources:** saved first-touch landing-page source/campaign, joins, UID
  submissions and current verification rate for those new users. Missing
  attribution is labeled Direct / unknown.
- **Retention:** exact day-1 and day-7 returns after joining. Only users whose
  return day is complete enter the denominator; today's partial day is excluded.
- **Users:** paginated stored-user list with activity and account status. Tap a
  user, or send `/userstats <Telegram ID>`, for a paginated event timeline.
- **CSV downloads:** overview, buttons, funnel, drop-offs, daily activity,
  sources, retention, users and events. Users.csv includes **all stored users**,
  including pre-existing accounts; its click totals use the chosen period.

Large exports are split into 50,000-row parts. Tap **Next CSV part** until the
final part. All parts share an activity cut-off, including when downloaded across
midnight; account fields reflect current status when each part is generated.
Dates contain the +05:30 offset. CSVs are UTF-8 with a BOM for Excel; user-controlled
values that could become spreadsheet formulas are escaped.

### Tracking boundaries

Detailed tracking starts when the new schema is first deployed. Existing join
dates, usernames, language, attribution and account status remain reportable.
Old detailed click paths and timestamps cannot be recovered from daily aggregates.
The `/stats` report below retains the earlier daily aggregate history.

Native Telegram callbacks are recorded with unique callback IDs, so replays do
not inflate tap counts. Incoming messages record only their category, not their
body, account ID, screenshot, or command arguments. The analytics event log stores
Telegram IDs, action labels, timestamps and screen identifiers. User reports join
the existing user/account records. Admin and group activity is excluded.

Screen views mean **successful delivery**, not read receipts. Video plays,
completion/watch time, file downloads and visits outside the tracked buttons are
not observable by the bot. No such measures are inferred.

URL buttons in newly delivered bot screens use opaque redirect links on
`ANALYTICS_LINK_BASE` (default: the existing `https://track.topxtracker.online`
service). The original destination and affiliate query parameters are preserved.
Set this variable to an empty string to keep direct URLs and disable URL tracking.
Native callbacks continue to be counted independently. Previously sent direct
links cannot be tracked retroactively.

The `/r/{token}` endpoint counts **link opens**, separately from authenticated
Telegram callback taps. Known preview crawlers, HEAD and prefetch requests are
excluded; repeat opens of the same link are deduplicated in 30-minute time buckets.
Unknown crawlers or forwarded links can still affect these estimates. No IP
addresses or user-agent strings are saved by this feature. Tokens contain no
Telegram ID or destination, redirects disable caching and referrer forwarding,
and only destinations created by the bot can be opened. Link creation failures
fall back to original URLs; tracking failures never prevent bot actions.

### Verification

`test_analytics.py` covers admin access, metadata-only tracking, link destination
preservation, preview filtering, delivery failures, CSV safety and continuation.
`test_analytics_sql.cjs` runs all SQL reports against an in-memory PostgreSQL
engine with synthetic users, including out-of-order funnel events and repeat taps.
Install its test-only runtime with
`npm install --prefix .testdeps/analytics-sql --ignore-scripts @electric-sql/pglite@0.5.10`,
then set `NODE_PATH` to that directory's `node_modules` and run
`node test_analytics_sql.cjs` with the project's Python dependencies available.
The runtime is not needed in production. Both tests avoid production data.

## Existing /stats report

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
