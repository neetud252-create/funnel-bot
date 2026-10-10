# Screenshot Test Signals

Home → Test Signals → trial screen with remaining signals → Test Signals → upload
one chart photo/image document → analysis replying to that image. Results show
Trend, Momentum, BUY/SELL Prediction and Timer in the compact reference layout.
Prediction uses the owner's level-reaction strategy. Incomplete or conflicting
confirmation adds a translated `(tentative)` suffix; an unreadable chart produces
an upload-retry error, not a signal. Reason and invalidation remain in the validated
provider response but are not displayed as extra paragraphs. Timer displays the
observed chart candle interval (for example, `1m` becomes `1 minute`), not an expiry
recommendation; the upload screen explains this. An unreadable interval stays
`Not visible`. The trial screen normally says `Accuracy: Not yet verified`.
For a temporary UI preview on your testing deployment, set `CHART_UI_TEST_COPY=1`
to show `Accuracy: 95% + Guaranteed` without a visible placeholder suffix.
This is sample copy; no measured accuracy supports a percentage or guarantee.
Unset the variable or set it to `0` before launch to restore `Not yet verified`.
This setting affects only the intro copy, not verification or signal limits.
The strategy requires readable price history, not a specific broker layout.
It accepts candlestick/OHLC, Heikin Ashi, line, area and Renko chart inputs from
any market, including OTC. Missing asset/timeframe labels do not by themselves
invalidate readable price structure. Non-OHLC styles use tentative directional
adaptations, never invented wicks, closes or confirmed candlestick entries.
These are supported input rules, not evidence of predictive accuracy across
chart styles or markets. No implementation can reliably read every screenshot.
Main menu → Get a signal now opens the same screenshot-upload prompt directly
for verified users. It no longer opens the manual mode/pair/expiry selection.
The button's text, style and `menu:signal` callback are unchanged. Any previous
manual countdown is cancelled when opening the uploader. Existing chart request
budgets and tier signal counters are unchanged; screenshot analysis still uses
the separate chart budget described below, not the main menu's 30/70 allowance.

Set `GEMINI_API_KEY` in Railway's service Variables (never commit it). An unset key
leaves a localized unavailable message; the rest of the bot still starts normally.
The provider is Google Gemini, pinned to `gemini-3.8-flash`, through the
`v1beta/models/gemini-3.8-flash:generateContent` REST endpoint. It sends inline
image bytes with a JSON Schema response, high media resolution and low thinking
level. Uses the existing httpx dependency; no new SDK is required. The key goes
only in the `x-goog-api-key` header, never the URL, logs or Telegram messages.
No DeepSeek/OpenAI credential, alternate model, paid tool or provider fallback is
used. Old `DEEPSEEK_*` variables are ignored, not deleted from Railway.

**Free-only deployment:** use a Google AI Studio project with billing disabled.
There is no per-request switch that makes a paid project's key free; the bot
cannot establish billing status from the key. Do not enable Cloud Billing or
assume an existing key belongs to a free project. Check its active quotas in
AI Studio. Google's shared project quotas also cover other apps using that key;
the bot's 200/day local cap is not a promise of 200 free Gemini requests. A 429
returns the existing localized busy message with New Analysis, without retries,
spend increases or a paid fallback. Google may have different daily reset times
from the bot's local UTC counters.

Unverified users have **two lifetime free BUY/SELL signals**. The database's
existing `users.verified` flag unlocks subsequent analysis; submitting an ID or
tapping a button is not verification. Results keep the blue New Analysis and green
Back buttons. After the second result, New Analysis opens the exhausted-trial screen
with Get Bot Access. Both callback entry points and uploads check the limit.
`/start`, a new day, Back and redeploying cannot reset it.
The older/manual signal flow requires the same stored verification flag at entry
and again before delivering a result. Old buttons, Premium status and token
balances cannot bypass the lifetime trial by opening a separate daily allowance.

`chart_trial_slots` reserves slots transactionally before image processing, using
a shared PostgreSQL advisory lock. Duplicate Telegram message IDs cannot consume
or produce another trial signal. Pending reservations expire after three minutes
to recover from a process crash. A late response cannot consume an expired slot.
Invalid uploads, API errors and cancelled sessions release pending slots.
Both confirmed and tentative directional results commit a slot before Telegram delivery. Explicit Telegram
rejections refund it; a timeout/unknown delivery retains it to prevent a third
signal after an uncertain send. A crash between committing and sending can retain
a slot without delivery; the ledger exposes that conservative limitation.

Defaults: `CHART_DAILY_LIMIT=5` requests per user, `CHART_GLOBAL_DAILY_LIMIT=200`
across all users, reset at 00:00 UTC. These budgets are separate from paid-tier
signal counts and persist in Postgres `chart_analysis_usage`. Counts are reserved
before an API attempt, including failed/cancelled attempts that might be billed.
No automatic retries. A 30-second cooldown and four concurrent requests per
process limit duplicate submissions. These are separate abuse/spending controls:
Errors do not use lifetime signal slots, but API attempts use daily budgets.
Verified users retain these existing daily request limits.

Only private chats; one JPEG/PNG/WebP, at most 8 MiB. Reject albums and unsupported
documents. Bytes stay in memory and are sent as base64 to Google Gemini; no Telegram URL,
bot token, username or chat history is sent. Caption context is capped at 500
characters. The upload screen names Gemini and asks users to crop personal
details. Google's unpaid-services terms allow content use for product improvement
and human review (with regional exceptions). Do not send sensitive, confidential
or personal information: use chart-only images without balances/account details.
No conversation history or Files API upload is used; this is not a provider-wide
data-retention guarantee.

All UI labels/errors are translated into the 12 existing languages. Analysis
prose is requested in the user's selected language. Model output is validated,
length-limited and HTML-escaped. No numeric confidence scores or fabricated expiry.
Back or /start abandons the session and suppresses late analysis results.

## Strategy interpretation: level-reaction-v3-chart-types

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
5. Sometimes pauses after dojis or losses, despite earlier saying every candle
   would be traded. At the owner's request, v2 nevertheless selects a direction
   on readable charts and discloses missing confirmation rather than abstaining.

Version 3 preserves that directional selection and adapts the observable
level-reaction principles to line/area swings and transformed Heikin Ashi/Renko
structure. Those adaptations were requested for broader chart support; they
are not additional claims from the original lesson. The prompt focuses on the
main price panel and ignores surrounding broker/app UI. An ambiguous selection
of multiple charts, unreadable history, oscillator-only image or non-chart is
still invalid input. Successful signals are BUY or SELL; errors are not signals.

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
candle to label an entry confirmed is a screenshot adaptation, not the narrator's
exact timing. A tentative estimate can be returned without full confirmation.
No arbitrary body-size or wick-ratio thresholds have been attributed to him.

When no full entry setup is visible, compare recent level reactions and failed
follow-through, then recent completed-candle structure/net progress and momentum.
Lower highs/lows and strong selling with weak bounces support SELL continuation;
higher highs/lows and strong buying with weak pullbacks support BUY continuation.
Do not invent an exhaustion reversal just because price has moved a long way.
In a mixed range, prioritize the latest completed reaction, then net progress,
then the latest clearly completed non-flat candle as a weak tie-breaker. Disclose
that weak basis. This ordering is an explicit directional-selection adaptation,
not a claim that the transcript specified a validated signal for every candle.

### Handling contradictions and missing information

- A new swing high or a big green candle is not automatically SELL. A clean
  breakout and a failed breakout are different; require the stated reaction.
- The narrator's failed dark-cloud example only removes bearish confirmation.
  It cannot by itself establish a confirmed BUY; reassess the remaining evidence.
- Repeated dojis/downward-shifting closes can show weakness, but an unfinished
  candle is not confirmation. Mixed evidence produces a tentative direction with
  a tentative label; missing confirmation or opposing evidence remains in the
  provider's internal explanation.
- The numbers 10, 16, 20, 30, 36 and 40 are shortened prices from that chart.
  They are not hardcoded levels for other markets. Multiples of five only add
  context where the instrument's scale and actual reactions justify it.
- A line drawn on the chart is not evidence by itself; left-side reactions must
  establish the level. Exact unreadable price labels are never invented.
- Claims about who deliberately caused a last-second close below 36 cannot be
  inferred from a still image. Do not implement an automatic BUY from that story.
- A screenshot cannot show the full tick sequence or guarantee it is current.
  Use the most recent clearly completed candle; no timed entry, expiry, or claim
  that the next candle must be green/red. Newer invalidating price action requires
  reassessing the current direction rather than repeating an obsolete setup.
- The transcript's 'sure shot', 80–100%, repeated entries after losses and profit
  target are not implemented. A candle's final color alone also does not establish
  whether a trade placed partway through it won.

### Validation boundary

Gemini returns a setup code, direction, trend, momentum, evidence, reason and boolean checks for
readability, closed candle, established level, reaction, follow-through and
conflict. The bot requires a readable chart, a BUY/SELL direction matching the
setup and two distinct evidence statements. A candlestick/OHLC chart type, all
five positive checks and no conflict are needed to label an entry confirmed. Missing confirmation changes
the setup to directional_buy/directional_sell and adds a translated `(tentative)`
suffix; it does not change the selected direction. An unreadable chart permits null
in the provider schema solely to request a clearer image; null is never a signal.
Contradictory direction/setup codes, truncated/blocked/malformed API output and
legacy abstention outcomes produce errors, never an invented fallback direction.
These checks validate the model's reported consistency;
they cannot independently prove its reading of image pixels is correct.

Offline tests cover transport, limits, setup/direction consistency, incomplete
confirmation, chart-type handling, duplicate evidence, prompt isolation, translations and navigation.
They do not measure trading accuracy. Before relying on results, evaluate a fixed
set of timestamped screenshots with later outcomes withheld during inference.
A live API/schema check validates integration, not predictive accuracy.
No image/outcome trading-accuracy evaluation has been completed.

On 10 October 2026, one owner-approved Quotex candlestick screenshot was tested
against the previous DeepSeek implementation. The API returned HTTP
200/completed, and the parser accepted a tentative SELL (`directional_sell`).
This verifies that single sample's provider/format path, not the cause of the
earlier rejection, other chart styles or the correctness of the prediction.
It is not a Gemini integration test.

On 10 October 2026, after the owner confirmed free-tier use, one anonymous
synthetic candlestick chart passed a live Gemini 3.8 Flash integration check.
The production request/parser returned a valid BUY/SELL result and the Telegram
result formatter accepted it in 7.2 seconds. No personal screenshot was sent.
This checks API/schema/rendering compatibility, not real-market accuracy.

### Response compatibility and diagnostics

The parser requires exactly one Gemini candidate with `finishReason=STOP` and
no prompt block. It ignores thought-summary text, and rejects tool/image output,
missing finish reasons, safety/refusal blocks and `MAX_TOKENS` truncation. It
accepts harmless whitespace/enum casing, a single whole JSON code fence and
extra result metadata (discarded). Missing chart-type metadata becomes
`unknown`, never a confirmed candlestick setup. It does not extract BUY/SELL
from arbitrary prose, repair truncated JSON, accept duplicate keys, override
contradictory directions/setups, or invent missing evidence. Non-OHLC chart types
always remain tentative even if the model incorrectly claims full confirmation.

Rejections log a fixed reason code (for example `max_output_tokens`, `json`,
`contradictory_direction`) and numeric token usage only, not model text, images,
captions or credentials. A provider/format error now uses the existing service
error wording instead of blaming screenshot clarity, and offers New Analysis.
Neither an invalid response nor a failed API call consumes a lifetime signal.
The existing daily request budgets and no-retry policy remain unchanged.
More reliable parsing does not establish prediction accuracy.

On 10 October 2026 at 15:38 IST, the previous DeepSeek deployment logged
`detail=max_output_tokens output_tokens=4000`: the provider had exhausted the
combined reasoning/final-answer allowance, not rejected the image as unreadable.
The Gemini request permits 8,192 output tokens with low thinking level and the
same strategy/validation. The HTTP timeout is 80 seconds with a 90-second
overall provider deadline, still below the existing three-minute trial lease.
This does not increase the user's daily request count, add automatic retries,
or accept partial JSON. Responses remain bounded; exhaustion is still an error.

`python test_chart_live.py path/to/chart.png` is an optional **single-request**
diagnostic using `GEMINI_API_KEY` and the same HTTP path as production. First
confirm billing is disabled. It sends that image to Google, so use only an
approved chart-only sample with all personal/account details removed. It prints
safe response metadata and validation results; it does not touch Telegram or
the database. With no argument it skips without making a request.

Checks: `python test_chart_signals.py`, `python test_home_menu.py`,
`python test_localization.py`, `python test_uid_singleflight.py`.
`CHART_TEST_DATABASE_URL=... python test_chart_trial_db.py` tests the real Postgres
quota logic in an isolated temporary schema that is removed afterwards.

API references:
- https://ai.google.dev/api/generate-content
- https://ai.google.dev/gemini-api/docs/generate-content/structured-output
- https://ai.google.dev/gemini-api/docs/generate-content/thinking
- https://ai.google.dev/gemini-api/docs/pricing
- https://ai.google.dev/gemini-api/docs/rate-limits
- https://ai.google.dev/gemini-api/terms
