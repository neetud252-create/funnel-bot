# Screenshot Test Signals

Home → Test Signals → upload one chart photo/image document → BUY/SELL directional
bias based on the owner's level-reaction strategy, or WAIT if unconfirmed.
The strategy requires readable candlesticks, the relevant level and left-side
history. It works with screenshots from any market, including OTC; that is an
input capability, not evidence of predictive accuracy across those markets.
Existing verified-user signals remain separate and unchanged.

Set `GEMINI_API_KEY` in Railway's service Variables (never commit it). An unset key
leaves a localized unavailable message; the rest of the bot still starts normally.
`GEMINI_MODEL` defaults to `gemini-3.8-flash`. It uses the Google Interactions REST
API with inline images, a JSON Schema response, low thinking and standard service
tier. An override must support these capabilities. Google lists a free tier for
this model as of 9 October 2026. Model/account access still requires a live test.
Uses the existing httpx dependency. No OpenAI credential or fallback is used.

To meet the owner's no-cost requirement, use an AI Studio project on the free
tier. A key alone does not reveal billing status; the bot cannot certify that a
project is free. Do not enable paid billing or paid fallback without the owner's
instruction. Free-tier RPM/TPM/RPD limits are model/project-specific; look up the
actual limits in AI Studio. Quota exhaustion returns a retry message, not another
provider/model, key rotation, automatic upgrade or a made-up signal. Google resets
daily provider quotas at midnight Pacific; the bot's own UTC budget is separate.

Defaults: `CHART_DAILY_LIMIT=5` requests per user, `CHART_GLOBAL_DAILY_LIMIT=200`
across all users, reset at 00:00 UTC. These budgets are separate from paid-tier
signal counts and persist in Postgres `chart_analysis_usage`. Counts are reserved
before an API attempt, including failed/cancelled attempts that might be billed.
No automatic retries. A 30-second cooldown and four concurrent requests per
process limit duplicate submissions. These are local controls, not a promise of
200 free Gemini requests or a dollar budget.

Only private chats; one JPEG/PNG/WebP, at most 8 MiB. Reject albums and unsupported
documents. Bytes stay in memory and are sent as base64 to Google Gemini; no Telegram URL,
bot token, username or chat history is sent. Caption context is capped at 500
characters. `store=false` opts out of Interactions request/response storage; it
does not override Google's free-tier data-use terms. The upload screen names
Google Gemini, asks users to crop personal details and states that free-tier
submissions may be used to improve Google's products.

All UI labels/errors are translated into the 12 existing languages. Analysis
prose is requested in the user's selected language. Model output is validated,
length-limited and HTML-escaped. No numeric confidence scores or fabricated expiry.
Back or /start abandons the session and suppresses late analysis results.

## Strategy interpretation: level-reaction-v1

Source: the owner's Hindi trading-session transcript supplied on 9 October 2026
(begins with an expanded candle described as exhaustion). The transcript contains
discretionary commentary, self-corrections, admitted losing trades and promotional
claims. It provides no quantitative thresholds, unedited dataset or validated win
rate. We encode the observable price-action ideas below, not its performance claims.
`chart_strategy.py` is the versioned prompt and consistency gate.

### What the narrator is doing

1. Finds an earlier buyer/seller zone in the left-hand candles, then watches the
   current candle at that zone. A zone gains relevance through actual reactions.
2. Compares body size, wicks, closing location and subsequent follow-through.
   A large candle after smaller ones raises the *possibility* of exhaustion.
3. Revises the original bias when the side expected to act fails. In the lesson,
   repeated failed buyer bounces plus strong selling override an old support idea.
4. Uses a prior support becoming resistance as a polarity change, then looks at
   how the next approach responds to the changed level.
5. Sometimes waits after dojis or losses, despite earlier saying every candle
   would be traded. The implemented version consistently waits for confirmation.

### Signal rules

| Setup | BUY condition | SELL condition |
|---|---|---|
| Rejection | Established support holds, with rejection and a closed bullish confirmation | Established resistance rejects, with a closed bearish confirmation |
| Failed breakout | Price probes below support, reclaims it and confirms upward | Price probes above resistance, returns below and confirms downward |
| Polarity retest | Broken resistance is retested from above and holds as support | Broken support is retested from below and rejects as resistance |
| Exhaustion reversal | Expanded bearish candle at support, failed continuation, then bullish confirmation | Expanded bullish candle at resistance, failed continuation, then bearish confirmation |
| Failed follow-through | Selling fails at a buyer zone and completed candles confirm buyer control | Buying fails at a seller zone and completed candles confirm seller control |

Mirrored cases (for example resistance becoming support and bearish exhaustion)
are implementation generalizations of the same idea; not every mirrored case was
explicitly demonstrated in the transcript. Requiring a completed confirmation
candle is a conservative screenshot adaptation, not the narrator's exact timing.
No arbitrary body-size or wick-ratio thresholds have been attributed to him.

### Handling contradictions and missing information

- A new swing high or a big green candle is not automatically SELL. A clean
  breakout and a failed breakout are different; require the stated reaction.
- The narrator's failed dark-cloud example only removes bearish confirmation.
  It cannot establish BUY without separate bullish confirmation at the level.
- Repeated dojis/downward-shifting closes can show weakness, but an unfinished
  candle is not confirmation. Mixed signals or strong opposing momentum → WAIT.
- The numbers 10, 16, 20, 30, 36 and 40 are shortened prices from that chart.
  They are not hardcoded levels for other markets. Multiples of five only add
  context where the instrument's scale and actual reactions justify it.
- A line drawn on the chart is not evidence by itself; left-side reactions must
  establish the level. Exact unreadable price labels are never invented.
- Claims about who deliberately caused a last-second close below 36 cannot be
  inferred from a still image. Do not implement an automatic BUY from that story.
- A screenshot cannot show the full tick sequence or guarantee it is current.
  Use the most recent clearly completed candle; no timed entry, expiry, or claim
  that the next candle must be green/red. Newer invalidating price action → WAIT.
- The transcript's 'sure shot', 80–100%, repeated entries after losses and profit
  target are not implemented. A candle's final color alone also does not establish
  whether a trade placed partway through it won.

### Validation boundary

Gemini returns a setup code, direction, evidence, reason and boolean checks for
readability, closed candle, established level, reaction, follow-through and
conflict. The bot requires a direction matching the setup, all five positive
checks, no conflict, and two distinct evidence statements. Contradictions become
WAIT with a translated explanation. Truncated/blocked/malformed API output is an
error, never a signal. These checks validate the model's reported consistency;
they cannot independently prove its reading of image pixels is correct.

Offline tests cover transport, limits, setup/direction consistency, incomplete
confirmation, duplicate evidence, prompt isolation, translations and navigation.
They do not measure trading accuracy. Before relying on results, evaluate a fixed
set of timestamped screenshots with later outcomes withheld during inference.
No such image/outcome evaluation or live Gemini call has been completed yet.

Checks: `python test_chart_signals.py`, `python test_home_menu.py`,
`python test_localization.py`, `python test_uid_singleflight.py`.

API references:
- https://ai.google.dev/gemini-api/docs/image-understanding
- https://ai.google.dev/gemini-api/docs/structured-output
- https://ai.google.dev/api/interactions-api
- https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash
- https://ai.google.dev/gemini-api/docs/pricing
- https://ai.google.dev/gemini-api/docs/rate-limits
