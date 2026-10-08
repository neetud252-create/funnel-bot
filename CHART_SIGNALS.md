# Screenshot Test Signals

Home → Test Signals → trial screen with remaining signals → Test Signals → upload
one chart photo/image document → analysis replying to that image. Results show
trend, momentum, BUY/SELL directional bias based on the owner's level-reaction
strategy (or WAIT if unconfirmed), visible chart timeframe, reason and invalidation.
The chart timeframe is not an invented one-minute expiry or recommended timer.
The strategy requires readable candlesticks, the relevant level and left-side
history. It works with screenshots from any market, including OTC; that is an
input capability, not evidence of predictive accuracy across those markets.
Existing verified-user signals remain separate and unchanged.

Set `DEEPSEEK_API_KEY` in Railway's service Variables (never commit it). An unset key
leaves a localized unavailable message; the rest of the bot still starts normally.
`DEEPSEEK_MODEL` defaults to `deepseek-flash`. It uses DeepSeek's `/responses` REST
API with inline images, a JSON Schema response and low reasoning effort. An
override must support these capabilities. Uses the existing httpx dependency.
No Gemini/OpenAI credential or provider fallback is used. DeepSeek API usage is
billed to the owner under their provider plan; the user trial does not mean the
API itself is free. The existing Railway credential was reused on 9 October 2026.

Unverified users have **two lifetime free BUY/SELL signals**. The database's
existing `users.verified` flag unlocks subsequent analysis; submitting an ID or
tapping a button is not verification. After the second result, Get Bot Access
replaces New Analysis. Both callback entry points and uploads check the limit.
`/start`, a new day, Back and redeploying cannot reset it.

`chart_trial_slots` reserves slots transactionally before image processing, using
a shared PostgreSQL advisory lock. Duplicate Telegram message IDs cannot consume
or produce another trial signal. Pending reservations expire after three minutes
to recover from a process crash. A late response cannot consume an expired slot.
WAIT, invalid uploads, API errors and cancelled sessions release pending slots.
Confirmed signals commit a slot before Telegram delivery. Explicit Telegram
rejections refund it; a timeout/unknown delivery retains it to prevent a third
signal after an uncertain send. A crash between committing and sending can retain
a slot without delivery; the ledger exposes that conservative limitation.

Defaults: `CHART_DAILY_LIMIT=5` requests per user, `CHART_GLOBAL_DAILY_LIMIT=200`
across all users, reset at 00:00 UTC. These budgets are separate from paid-tier
signal counts and persist in Postgres `chart_analysis_usage`. Counts are reserved
before an API attempt, including failed/cancelled attempts that might be billed.
No automatic retries. A 30-second cooldown and four concurrent requests per
process limit duplicate submissions. These are separate abuse/spending controls:
WAIT/errors do not use lifetime signal slots, but API attempts use daily budgets.
Verified users retain these existing daily request limits.

Only private chats; one JPEG/PNG/WebP, at most 8 MiB. Reject albums and unsupported
documents. Bytes stay in memory and are sent as base64 to DeepSeek; no Telegram URL,
bot token, username or chat history is sent. Caption context is capped at 500
characters. DeepSeek documents Responses as stateless; this does not establish a
provider-wide data-retention guarantee. The upload screen names DeepSeek and asks
users to crop personal details.

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

DeepSeek returns a setup code, direction, trend, momentum, evidence, reason and boolean checks for
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
A live DeepSeek call with the owner's chart passed image/schema validation on
9 October 2026 and returned WAIT. That validates integration, not predictive
accuracy. No image/outcome trading-accuracy evaluation has been completed.

Checks: `python test_chart_signals.py`, `python test_home_menu.py`,
`python test_localization.py`, `python test_uid_singleflight.py`.
`CHART_TEST_DATABASE_URL=... python test_chart_trial_db.py` tests the real Postgres
quota logic in an isolated temporary schema that is removed afterwards.

API references:
- https://api-docs.deepseek.com/api/create-response/
- https://api-docs.deepseek.com/guides/vision/
- https://api-docs.deepseek.com/
