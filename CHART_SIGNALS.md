# Screenshot Test Signals

Home → Test Signals → upload one chart photo/image document → BUY/SELL directional
bias with visible evidence and invalidation, or WAIT when evidence is insufficient.
Supports readable price charts across markets, including OTC. There is no live
data feed, trade execution, guaranteed prediction or random fallback in this flow.
Existing verified-user signals are separate and unchanged.

Set `OPENAI_API_KEY` in Railway's service Variables (never commit it). An unset key
leaves a localized unavailable message; the rest of the bot still starts normally.
`OPENAI_MODEL` defaults to `gpt-4.1-mini`; any override must support image inputs,
Responses and strict structured outputs. Model/account access must be tested with
the configured API project. Uses the existing httpx dependency.

Defaults: `CHART_DAILY_LIMIT=5` requests per user, `CHART_GLOBAL_DAILY_LIMIT=200`
across all users, reset at 00:00 UTC. These budgets are separate from paid-tier
signal counts and persist in Postgres `chart_analysis_usage`. Counts are reserved
before an API attempt, including failed/cancelled attempts that might be billed.
No automatic retries. A 30-second cooldown and four concurrent requests per
process limit accidental duplicate submissions. Set provider project spending
limits separately; request counts are not a dollar budget.

Only private chats; one JPEG/PNG/WebP, at most 8 MiB. Reject albums and unsupported
documents. Bytes stay in memory and are sent as base64 to OpenAI; no Telegram URL,
bot token, username or chat history is sent. Caption context is capped at 500
characters. `store=false` disables Responses storage; it is not a promise of
zero provider retention. The upload screen tells users where the image goes.

All UI labels/errors are translated into the 12 existing languages. Analysis
prose is requested in the user's selected language. Model output is validated,
length-limited and HTML-escaped. No numeric confidence scores or fabricated expiry.
Back or /start abandons the session and suppresses late analysis results.

Checks: `python test_chart_signals.py`, `python test_home_menu.py`,
`python test_localization.py`, `python test_uid_singleflight.py`.

API references:
- https://developers.openai.com/api/docs/guides/images-vision
- https://developers.openai.com/api/docs/guides/structured-outputs
- https://developers.openai.com/api/docs/models/gpt-4.1-mini
