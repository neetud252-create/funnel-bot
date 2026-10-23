# Pocket IQ asset selection

The public signal entry points (`/assets`, `/markets`, `/signals`, the welcome
trial button, and the dashboard's Get Signals button) use:

Market (OTC / FIN) → Category → Asset → Candle timeframe → Chart and analysis.

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

`market_catalog.py` joins names to the authorized OTCharts `/v1/symbols?venue=otc`
response. Only that response supplies executable symbol identifiers. New listings
not in the seed remain browsable under Other assets. The feed's five-minute
catalogue cache detects plan changes without a deployment. Updating the static
broker catalogue requires updating the JSON snapshot and reviewing its source.

Only exact OTC matches with connected data offer the 1-minute / 5-minute candle
buttons. Missing, closed, or unavailable assets give a short explanation. FIN
needs its own matching feed; upgrading an OTC plan does not create FIN coverage.
No external spot-FX price is substituted for a broker OTC series.

`OTCHARTS_API_KEY` remains a Railway secret. `MARKET_DATA_PUBLIC_ENABLED=false`
keeps live analysis in the existing admin preview until feed-sharing eligibility
is resolved. This flag does not hide the public catalogue. No paid subscription
is created or changed by this update.

## Analysis and migration

Validated completed OHLC candles generate a PNG chart for the existing analysis
provider. Freshness, continuity, response validation, WAIT for an unconfirmed
setup, concurrency, cancellation, and transactional usage accounting remain.
Only a successfully delivered analysis consumes the shared lifetime/daily quota.
Unsupported entries are rejected before reserving a trial slot or calling AI.

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
