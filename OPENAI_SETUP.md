# OpenAI chart analysis

In Railway, select pleasant-playfulness → production → worker.
Add `OPENAI_API_KEY` as a secret variable and set `CHART_PROVIDER=openai`,
then deploy the integration commit and variable changes.
Never put the key in Git, chat, or diagnostic logs.

The adapter uses the OpenAI Responses API with `gpt-6-luna`, low reasoning,
high-detail screenshot input, strict JSON output, and `store=false`.
The existing chart strategy, animation, request caps, concurrency limit,
90-second deadline, and refund behavior remain in place.
Each transient retry consumes the existing durable API attempt budget.
HTTP 429 and credential/billing errors stop without retrying or switching providers.

Without CHART_PROVIDER, the current Gemini configuration remains active.
An explicit OpenAI selection with a missing key fails configuration; it never
silently uses Gemini. To roll back, set CHART_PROVIDER=gemini and redeploy.

Verify startup reports provider=openai and API configured=True. Then send a
non-sensitive chart screenshot to the bot and verify a completed response and
provider timing logs. Actual latency and account/model access require this live
check; offline tests do not establish them.

Run offline contracts:

```sh
python -m unittest test_chart_openai test_chart_signals test_chart_progress test_localization test_signal_access -q
```
