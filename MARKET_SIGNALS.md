# Pocket IQ asset selection

The public signal entry points (`/assets`, `/markets`, `/signals`, the welcome
trial button, and the dashboard's Get Signals button) use:

Market (OTC / FIN) → Category → Asset → Expiration → Chart and BUY/SELL analysis.

Screens use the existing trading_type, asset_category and currency_pair banners.
Asset pages have six items in two columns, a page indicator with previous/next
arrows, and a full-width red Back button. The 57 OTC currency pairs retain the
owner's supplied order across ten pages. Search by typing a name inside a category.

## Catalogue versus live data

`pocket_assets.json` contains 172 published entries: 112 OTC and 60 FIN, checked
against Pocket Option's [OTC schedule](https://pocketoption.com/en/assets-otc/)
and [general schedule](https://pocketoption.com/en/assets/) on 2026-10-24.
It records names and categories, not payouts, opening status, or price data.
The catalogue remains browsable without a feed key, during outages, and after
an analysis allowance is exhausted. An empty FIN commodity category is explicit;
we do not invent assets absent from the published catalogue.

`market_catalog.py` joins the seed to the selected feed's live catalogue, with
separate OTC and FIN identities. Only the feed supplies executable symbol IDs.
Additional direct-feed listings appear in their broker-supplied categories.
Closed assets remain browsable and are checked again before fetching candles.

Every catalogue asset opens the expiration banner and reference button grid:
locked S5; S10/S15; S20/S25/S30; S45/S50/S55; M1; M2/M3; M4/M5/M6;
M7/M8/M9; M10, followed by a red Back button. M1–M10 select an expiry in minutes.
Missing, closed, or unavailable assets also give a short explanation; choosing an
expiry never bypasses the live-data, access, or allowance gates. FIN uses the
direct broker feed's FIN symbols; market schedules still apply.
No external spot-FX price is substituted for a broker OTC series.

## Direct feed (no data subscription)

Set `MARKET_DATA_PROVIDER=pocketoption` and `MARKET_DATA_PUBLIC_ENABLED=true`.
The independent `pocket_feed.py` adapter connects to Pocket Option's public
guest-demo WebSocket with a short-lived guest token. It does not read account
credentials or `OTCHARTS_API_KEY`, install a broker automation library, expose
order methods, or fall back to a paid service. A single shared connection and
15-second demand cache combine concurrent requests. There is no all-asset
background poll. TLS hostname/certificate checks remain enabled.

The production adapter sends only guest `auth`, `changeSymbol`, and protocol
handshake/pong messages. Engine.IO/Socket.IO public wire references:
[guest session](https://github.com/ChipaDevTeam/BinaryOptionsTools-v2/blob/master/docs/api/python.md),
[event shapes](https://github.com/chema-creator/PocketOptionApi/blob/main/pocketoptionapi/ws/client.py).
The implementation is independent; neither third-party trading package is a
dependency. This is an unofficial endpoint, so ongoing availability and
broker-side protocol stability are not guaranteed. Hosting and AI costs remain.

Live checks on 2026-10-24 returned 183 symbols and matched all 172 seed entries.
Open/closed counts change with the broker schedule. The full open-asset audit
validated 115 of 118 symbols in one pass; three FIN crypto histories had gaps
and correctly failed. A catalogue listing is not a promise of valid candles.
M1 and M5 checks across all five categories, and fresh guest reconnections,
passed. Run `python scripts/check_pocket_feed.py --all --chart` for an updated
coverage report in ignored `test-results/`; this sends no Telegram messages,
makes no AI calls and places no trades.

The broker clock currently uses UTC+2. The adapter checks that a recent tick
matches that offset, rather than guessing an offset from potentially stale
history. The server OHLC list trails live ticks. Complete, contiguous tick
intervals supply recent bars; the truncated first interval and current forming
bar are excluded. Overlapping finalized OHLC must agree. The latest server M5
bar can be partial: completed M1 bars supply its exact five-minute tail. Missing
intervals are never forward-filled. Gaps, stale data and conflicting bars fail.

Connection failures use a short backoff. Timed-out/cancelled requests close their
socket so late responses cannot satisfy a later request. A deployment performs
one bounded read-only feed check and logs only counts, a symbol and candle age.

`MARKET_DATA_PUBLIC_ENABLED=false` retains the optional admin preview gate.
The former OTCharts adapter remains available only with an explicit
`MARKET_DATA_PROVIDER=otcharts` configuration (also the compatibility default
when the variable is absent) and a valid key. It is not a fallback. No paid
subscription is created, cancelled or changed by this update.

## Analysis and migration

Validated completed OHLC candles generate a PNG chart for the existing analysis
provider. New expiration callbacks always request completed 60-second candles;
expiry is a separate requested horizon included in the model context, result,
and New Analysis callback. Old 60/300-second chart callbacks retain their candle
period and map to M1/M5 expiry. No unsupported candle intervals are requested.

The existing level-reaction strategy returns BUY or SELL when the chart supports
a directional bias. Incomplete/mixed confirmation is labelled tentative, never
changed to WAIT. Unreadable charts, invalid responses, missing or stale candles
still produce errors, not invented directions. The expiry is user-selected,
not an assertion that the strategy has validated an optimal holding period.
Freshness, continuity, response validation, concurrency, cancellation, and
transactional usage accounting remain.
Only a successfully delivered analysis consumes the shared lifetime/daily quota.
Unsupported entries are rejected before reserving a trial slot or calling AI.

While candles and AI analysis load, `market_progress.py` sends the existing
standalone animated candlestick custom emoji followed by a short analysis notice.
There is no fixed delay, fake percentage, 30-second promise, or paid-speed upsell.
Telegram clients without custom-emoji support show its Unicode fallback.
Companion registration/cleanup is conditional on the current UI message, and a
navigation watcher removes only the old request's own loading messages.

Screenshot upload handlers are no longer installed. Uploaded media and old
`chart:*`, manual, pair, expiry, and New Signal callbacks open the new market
menu. Those redirects are registered before the retired callbacks in bot.py,
so old messages cannot invoke the previous random-signal or upload code.
The existing chart modules remain dependencies for analysis, usage records,
provider error messages, and historical tests; they are not public upload routes.
Welcome, pinned Start Here, help, FAQ and privacy text describe asset selection.

Tests: `test_market_signals.py` covers the complete pagination, unavailable data,
plan refresh, forged/stale callbacks, dispatch priority, navigation cancellation,
duplicate taps, shared usage, and chart delivery. `test_market_data.py` validates
the provider contract and candle integrity without live or paid requests.
`test_pocket_feed.py` covers the guest transport, clock validation, tick/history
merging, FIN identity, shared cache, authentication, reconnects, and cancellation.
