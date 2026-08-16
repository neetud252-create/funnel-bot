# PROJECT_CONTEXT.md

Technical context for the **funnel-bot** Telegram funnel bot.

- Repository: `C:\dev\funnel-bot` — remote `https://github.com/neetud252-create/funnel-bot.git`
- Branch: `main` (only branch used)
- Audit commit: `619853a` "Update welcome screen image" (2026-08-10)
- Audit date: 2026-08-10
- Audit type: **read-only**. No source file was modified, renamed, deleted, committed, or pushed. This document is the only file created.

> **Secrets:** every value in this document comes from tracked source files. A scan of all tracked files for tokens, API keys, database URLs, passwords and session strings returned **no matches** — no credentials are committed. Only environment-variable *names* appear below. `.env`, `session.txt`, `s.txt`, `*.session` and `session_out.txt` are gitignored and are not present in the repository.

---

## 1. PROJECT STRUCTURE

### Directory tree (all tracked files)

```
funnel-bot/
├── .gitignore
├── .python-version              # 3.12
├── Procfile                     # web: python bot.py
├── requirements.txt
├── bot.py                       # entrypoint: aiogram handlers + process supervisor
├── config.py                    # all screens, texts, emoji, env-backed settings
├── db.py                        # asyncpg pool, schema, queries
├── server.py                    # FastAPI postback receiver (runs beside the bot)
├── panelbot.py                  # Telethon verification against the affiliate panel bot
├── gen_session.py               # LOCAL one-off: mint a Telethon StringSession
├── trim.py                      # stale one-off maintenance script (see §9)
└── assets/                      # 19 .jpg + 1 .mp4 tracked, 1 .jpg untracked
    ├── access.mp4
    ├── final.jpg      gate.jpg       how.jpg        how3.jpg
    ├── howto.jpg      menu.jpg       mode.jpg       register.jpg
    ├── reviews1.jpg   reviews2.jpg   reviews3.jpg   reviews4.jpg
    ├── reviews5.jpg   success.jpg    support.jpg    tech.jpg
    ├── type.jpg       welcome.jpg
    └── asset.jpg.jpg              # UNTRACKED — malformed name, see §3
```

There is no `PROJECT_CONTEXT.md` in git history; this file is new and untracked.

### Purpose of each file

| File | Purpose |
|---|---|
| `bot.py` | The entrypoint and the whole handler layer. Defines the `Dispatcher`, every message/callback handler, the screen renderer (`render`/`show`), the keyboard builder (`build_kb`), the media caches, the signal countdown engine, and `main()` which runs uvicorn + long polling + the retry worker concurrently. |
| `config.py` | Pure data, no logic beyond env reads. Holds `SCREENS` (every screen's media key, caption and keyboard), the premium-emoji ID constants, the signal-flow strings, verification verdict messages, and all env-backed settings. |
| `db.py` | PostgreSQL access via `asyncpg`. Owns the connection pool, the `CREATE TABLE IF NOT EXISTS` schema applied at startup, and every query helper. |
| `server.py` | A FastAPI app exposing `/health` and `/postback/{secret}` for affiliate postbacks. Served by uvicorn inside the same process as the bot. |
| `panelbot.py` | Group F verification. Uses a Telethon **user** session to send `/user {uid}` to the affiliate panel bot and parse the reply. Degrades to "disabled" rather than crashing when creds are absent. |
| `gen_session.py` | One-off local helper to produce a `TELETHON_SESSION` string. Explicitly documented as "never run on the server". |
| `trim.py` | A one-off script that rewrites `config.py` in place, deleting screens and rewriting `"photo": "final"`. **Stale and dangerous** — see §9. |
| `Procfile` | Railway/Heroku process definition: `web: python bot.py`. |
| `requirements.txt` | Pinned dependencies. |
| `.python-version` | `3.12`. |
| `.gitignore` | Ignores venv, bytecode, `.env`, `assets/*.tmp`, `*.bak`, and all Telethon session artefacts. |

### Dependencies (`requirements.txt`)

```
aiogram==3.30.0     asyncpg==0.30.0     httpx==0.28.1
apscheduler==3.11.0 fastapi==0.115.6    uvicorn==0.34.0
python-multipart==0.0.20                telethon==1.36.0
```

`httpx` and `apscheduler` are pinned but **not imported anywhere** in the codebase.

---

## 2. TELEGRAM BOT FLOW

### Rendering mechanics (read this first)

Three functions in `bot.py` govern every screen:

- **`photo_for(key)`** (`bot.py:30-31`) → returns a cached Telegram `file_id` if the media was sent before, else `FSInputFile("assets/" + key + ".jpg")`. **This single line is why a config media key maps to a filename.** `video_for` is the `.mp4` twin.
- **`media_missing(key, ext)`** (`bot.py:36-41`) → `True` when there is no cached `file_id` *and* `assets/<key>.<ext>` does not exist on disk.
- **`render(...)`** (`bot.py:119-142`) → deletes the previous UI message (`users.ui_msg_id`), then sends the new one. If `media_missing` is `True` it logs `asset '<key>' missing …` at ERROR and **sends the screen as plain text with the keyboard intact** (`bot.py:128-133`). Buttons keep working; only the picture disappears. Finally it stores the new `message_id` as `ui_msg_id`.

Consequence: a missing asset degrades a screen, it never breaks navigation. Also, because `_photo_cache` survives only in memory, a missing file is masked until the process restarts.

`show(bot, tg_id, key)` (`bot.py:144-149`) looks up `config.SCREENS[key]` and dispatches to `render` with `is_video=True` when the screen declares `"video"` instead of `"photo"`.

**`build_kb`** (`bot.py:63-89`) turns config tuples `(label, action, style?, icon?)` into an `InlineKeyboardMarkup`. `url:` actions become URL buttons (and are **dropped with a warning** if the URL fails `_URL_OK`); everything else becomes `callback_data = action[3:]`, i.e. the `cb:` prefix is stripped.

### Journey: `/start` → final screen

#### Stage 1 — Subscription gate

| | |
|---|---|
| **Trigger** | `/start` → `start` handler, `bot.py:151-155` (`@dp.message(CommandStart())`) |
| **Actions** | `state.clear()`, `db.touch_user(...)`, `show(..., "gate")` |
| **Screen** | `gate` (`config.py:78-83`) |
| **Image key** | `gate` → **`assets/gate.jpg`** ✅ exists |
| **Caption** | "To continue, subscribe to the best Telegram channel about trading: …" |
| **Buttons** | `Subscribe to Channel` → URL `CHANNEL_URL` · `Check Subscription` → `check_sub` |
| **Next** | `check_sub` handler |

#### Stage 2 — Subscription check

`check_sub` (`bot.py:157-163`) calls `is_subscribed()` → `get_chat_member` against `CHANNEL_ID`, accepting statuses `creator/administrator/member` (and `restricted` only when `is_member`). Success → `cb.answer("Verified")` + `show("welcome")`. Failure → alert *"You have not joined the channel yet."* and the user stays put.

#### Stage 3 — Welcome

| | |
|---|---|
| **Trigger** | successful `check_sub` |
| **Screen** | `welcome` (`config.py:84-88`) |
| **Image key** | `welcome` → **`assets/welcome.jpg`** ✅ exists (277,141 B, MD5 `D444F31E…`, updated in `619853a`) |
| **Caption** | "🖐️ **Hello! I am Go+, your personal trading bot.** …" |
| **Buttons** | `Start` (style `success`, icon `E_FLASH`) → `go:how` |
| **Next** | `nav` → `how` |

#### Stage 4 — Why traders choose Go+

| | |
|---|---|
| **Trigger** | `go:how` |
| **Screen** | `how` (`config.py:89-93`) |
| **Image key** | `how` → **`assets/how.jpg`** ✅ exists — but byte-identical to `tech.jpg` (see §9) |
| **Caption** | "✨**Why traders choose Go+:** 100+ trading assets / OTC and exchange trading / …" |
| **Buttons** | `How Does It Work` (style `primary`, icon `E_QMARK`) → `go:tech` |
| **Next** | `nav` → `tech` |

#### Stage 5 — It is simple

| | |
|---|---|
| **Trigger** | `go:tech` |
| **Screen** | `tech` (`config.py:94-98`) |
| **Image key** | `tech` → **`assets/tech.jpg`** ✅ exists |
| **Caption** | "🤖 **It is simple:** 1 Select an asset / 2 Choose the expiration time / …" |
| **Buttons** | `See the technology` (`primary`, icon `E_GEAR`) → `go:ai` |
| **Next** | `nav` → `ai` |

#### Stage 6 — AI technology

| | |
|---|---|
| **Trigger** | `go:ai` |
| **Screen** | `ai` (`config.py:99-103`) |
| **Image key** | `ai` → `assets/ai.jpg` ❌ **MISSING** (deleted in `d92571a`) → text-only fallback |
| **Caption** | "⚙️**I am powered by advanced AI,** …" |
| **Buttons** | `See real results` (`primary`) → `results` |
| **Next** | `results` handler |

#### Stage 7 — Reviews album + results

`results` (`bot.py:165-177`) — the one screen that does **not** go through `render`:

1. `wipe()` deletes the previous UI message and any stored album message ids.
2. Sends a **media group** of `config.REVIEWS` = `reviews1…reviews5` → `assets/reviews1.jpg` … `assets/reviews5.jpg` ✅ all exist. Each returned message's `file_id` is cached.
3. Sends the results caption as a **plain text message** (`bot.py:175`), not a photo.

⚠️ `SCREENS["results"]` declares `"photo": "welcome"` (`config.py:105`) but the handler never reads it — **no welcome image is shown here.** The key is inert (a leftover from `trim.py`, see §9).

| **Buttons** | `Get access to Go+` (`success`, icon `5307843983102204243`) → `go:access` · `Open Telegram channel` → URL |
|---|---|
| **Next** | `nav` → `access` |

⚠️ `wipe()` reads `user["album_ids"]`, but **nothing ever calls `db.set_album` with the ids of the album it just sent** — only `set_album(tg_id, None)` on cleanup. Review albums are therefore never actually deleted.

#### Stage 8 — Access (video screen)

| | |
|---|---|
| **Trigger** | `go:access` |
| **Screen** | `access` (`config.py:110-115`) — uses `"video"`, so `render(is_video=True)` |
| **Media key** | `access` → **`assets/access.mp4`** ✅ exists (≈40.8 MB) |
| **Caption** | "💥 **This is where it starts.** …" |
| **Buttons** | `Activate Bot` (`success`) → `go:register` |
| **Next** | `nav` → `register` (**and** arms FSM state) |

#### Stage 9 — Register / UID capture

| | |
|---|---|
| **Trigger** | `go:register` |
| **Screen** | `register` (`config.py:116-122`) |
| **Image key** | `register` → **`assets/register.jpg`** ✅ exists |
| **Caption** | "🔐 **To access Go+, register for a new Pocket Option account using my link:** …" |
| **Buttons** | `🔑 Register & Get Access` → URL `REF_LINK` · `👥 How to register` → `go:howto` · `🙋 Support` → URL `SUPPORT_URL` |
| **Side effects** | `nav` sets FSM state `Reg.waiting_uid` and spawns `_register_nudge` (`bot.py:179-194`) — a ~4 s delayed follow-up sent only if the user is still parked on this screen. Re-entering cancels the previous nudge. |

Optional detour: `go:howto` → `howto` (`config.py:210-214`), image `assets/howto.jpg` ✅, single button `Back to registration` → `go:register`.

#### Stage 10 — UID validation

The user types their account ID. `capture_uid` (`bot.py:416-433`) wraps `_capture_uid` (`bot.py:435-462`) in a catch-all that re-arms the state and replies with `MSG_UID_ERROR`, because the user's message is deleted on arrival and no path may leave them with silence.

`_capture_uid`: delete the user's message → validate against `UID_RE = \d{5,15}` → reject non-numeric with a hint → reject a UID already owned by another `tg_id` → `db.save_uid_only` → `state.clear()` → `wipe()` → branch:

- **`TEST_MODE`** (`VERIFY_MODE=test`): logs a warning, `set_verified(tg_id, 0)`, `_show_menu(test_mode=True)` — the menu caption is prefixed with `MSG_TEST_MODE`. **No campaign or deposit check at all.**
- **live**: `_run_verification` (`bot.py:389-414`) sends an "⏳ Checking account" ack, then `panelbot.lookup_trader(uid)`:

| Outcome | Result |
|---|---|
| `campaign_id` matches `CAMPAIGN_ID` and `sum_deposits ≥ MIN_DEPOSIT` | `set_verified`, delete ack, `_show_menu` |
| campaign matches, deposit too low | `MSG_NEED_DEPOSIT` + register button |
| not found / different campaign | `MSG_WRONG_LINK` + register button |
| `PanelUnavailable` | `MSG_DELAYED`; `retry_worker` re-checks every 30 min and pushes the menu on success |

#### Stage 11 — Main menu (post-verification)

| | |
|---|---|
| **Trigger** | verification success, or `go:menu` |
| **Screen** | `menu` (`config.py:126-136`), rendered by `_show_menu` (`bot.py:382-387`) |
| **Image key** | `menu` → **`assets/menu.jpg`** ✅ exists |
| **Caption** | "🤖 **Go+ main menu** … Available today: {limit} signals / Used: {used} / Left: {left} … Your level: Start" — a template filled per user by `_show_menu`; the level is still static |
| **Buttons** | `🚀 Get a signal` → `menu:signal` · `🌲 My level` → `menu:level` · `🧑 Support`, `VIP team`, `Pocket Option`, `✈️ Telegram channel`, `▶️ YouTube channel` → URLs |

#### Stage 12 — Trading mode

`menu:signal` → `menu_signal` (`bot.py:215-218`) → `mode` (`config.py:142-148`), image **`assets/mode.jpg`** ✅.
Buttons: `✋ Manual` → `mode:manual` (→ `type`) · `🔓 Automatic` → `mode:auto` (→ "Coming soon 🚀" alert) · `« Back` → `go:menu`.

#### Stage 13 — Market type

`mode:manual` → `mode_manual` (`bot.py:221-224`) → `type` (`config.py:152-158`), image **`assets/type.jpg`** ✅.
Buttons: `🔹 OTC` → `type:otc` (→ `asset`) · `🔒 FIN` → `type:fin` (→ alert) · `« Back` → `menu:signal`.

#### Stage 14 — Asset category

`type:otc` → `type_otc` (`bot.py:227-230`) → `asset` (`config.py:164-173`), image key `asset` → `assets/asset.jpg` ❌ **MISSING** → text-only fallback.
Buttons: `🔄 Currency pairs` → `asset:forex` (→ `pairs`) · Cryptocurrencies / Stocks / Indices / Commodities → `asset:*` (→ alert) · `« Back` → `mode:manual`.

#### Stage 15 — Currency pair

`asset:forex` → `asset_forex` → `show_pairs(page=0)`, image key `currency_pair` → `assets/currency_pair.jpg` ✅.
Buttons: six OTC pairs per page → `pair:<code>` · `N/10` → `noop` (inert indicator) · `›` → `pairpage:<next>` → `pairs_page` · `« Back` → `type:otc`.

**57 OTC pairs over 10 pages.** The list lives in `config.PAIRS` (deduped from `PAIRS_RAW` by `_dedupe_pairs`) and is the single source of truth — the keyboard, the callback codes, the page count and the signal label all derive from it. `pair_code("AUD/CAD OTC")` → `audcad` (stable across restarts, unlike an index). `PAIR_PAGES` is recomputed on import as `ceil(len(PAIRS) / PAIRS_PER_PAGE)`, so adding a pair to `PAIRS_RAW` is the only edit needed. Grid is unchanged: 2 per row × 3 rows + the nav row + Back. `›` wraps from the last page back to page 1, so all 57 pairs are reachable with forward-only taps and no button was added to the layout. `SCREENS["pairs"]["kb"]` is `pairs_kb(0)`, so a plain `show(…, "pairs")` still renders page 1.

#### Daily signal quota

Each user gets **30 signals per day**, tracked per Telegram ID in `users.signals_used_today` / `users.last_reset_date` (both added by `ALTER TABLE … IF NOT EXISTS` in `db.SCHEMA`, so deploying needs no migration step). The limit is `config.DAILY_SIGNAL_LIMIT`, overridable via the `DAILY_SIGNAL_LIMIT` env var.

**Reset** is lazy, not scheduled: `db.signal_state` and `db.consume_signal` both treat a row whose `last_reset_date` is not `CURRENT_DATE` as 0-used and rewrite it on the spot. Nothing cron-like is needed, and a restart cannot clear the count because it lives in the table. ⚠️ `CURRENT_DATE` is the **Postgres server's** date (UTC on Railway) — "a new day" is midnight UTC for everyone, not per-user local midnight.

**Enforcement** happens in two places:
- `_start_signal` (the single entry point for both `m:*` and `new_signal`) reads `signal_state` and, at 0 left, answers the callback with `config.MSG_DAILY_LIMIT` as an alert and starts no countdown. It answers the callback itself, so `m_action`/`new_signal` must **not** call `cb.answer()` first — Telegram discards a second answer, which would silently swallow the alert.
- `_run_signal` calls `db.consume_signal` at **delivery** time, not at the tap. One atomic conditional `UPDATE … WHERE signals_used_today < $2 RETURNING …` is the real gate, so simultaneous taps cannot both pass. Spending the quota at delivery also means a countdown that was cancelled or superseded costs the user nothing.

**Menu numbers** are filled per render: `SCREENS["menu"]["text"]` is a `{limit}`/`{used}`/`{left}` template and `_show_menu` formats it. `show()` special-cases `"menu"` to route through `_show_menu`, so the `cb:go:menu` Back path can't render raw braces.

#### Stage 16 — Test menu (expiration picker)

`pair:*` → `pair_action`: records the chosen pair label into the in-memory `_pair_choice` via `config.PAIR_CODES` (code → label for the whole list, not just one page), then shows `test_menu` (`config.py:196-208`), image key `test_menu` → `assets/test_menu.jpg` ❌ **MISSING** → text-only fallback.

Buttons: `🔒 S5 … S55` → `s:*` → `s_action` → alert *"🔒 Locked. Coming soon"* (**S35 and S40 are intentionally absent**; the rows jump 30 → 45). `🔥 M1 … ✅ M10` → `m:*` → `m_action`.

#### Stage 17 — Analyzing countdown

`m_action` (`bot.py:314-320`) → `_start_signal` (`bot.py:302-312`) cancels any countdown already running for that user, records the expiration in `_expiry_choice`, and spawns `_run_signal` (`bot.py:272-300`).

`_run_signal` **edits the tapped message in place** — no new message, no image swap — from `00:30` down to `00:00` in 5 s steps (`SIGNAL_COUNTDOWN`/`SIGNAL_STEP`), rendering `SIGNAL_ANALYZING`. Edits carry **no `reply_markup`**, so the S/M grid disappears and nothing is tappable mid-analysis. `_edit_signal` picks `edit_message_caption` vs `edit_message_text` based on whether the tapped message was a photo, and swallows edit failures so one bad tick can't kill the countdown.

**Image shown: whatever the test-menu message already was.** Since `test_menu.jpg` is missing, that message is text — so the analyzing screen currently has **no image**.

Before the final swap it re-reads `ui_msg_id`; if another screen took over, the signal is dropped rather than clobbering it.

#### Stage 18 — BUY result (final screen)

`render(bot, tg_id, "buy", SIGNAL_RESULT…, SIGNAL_KB)` (`bot.py:291-293`) — image key `buy` → `assets/buy.jpg` ❌ **MISSING** → text-only fallback.

Text: "✅ The analysis is complete! / 💱 Currency pair: {pair} / ⏱️ Expiration time: {expiry} / 🔔 Signal: BUY 🟢🟢". Direction is **hardcoded to BUY**. `{pair}` is the label from `_pair_choice` (falls back to `DEFAULT_PAIR = "AUD/CAD OTC"` after a restart); `{expiry}` comes from the button (`m:5` → `M5`).

Button: `🚀 New Signal` → `new_signal` → `new_signal` handler (`bot.py:322-328`) → re-runs `_start_signal` on the signal message itself with the remembered expiration (default `M1`). **This is the end of the funnel — the loop closes here.**

#### Unreachable branch

`gallery` (`bot.py:354-362`) handles `gallery:*` and builds a keyboard containing `cb:go:final`. **No screen anywhere emits `cb:gallery:*`**, so this handler is dead code. If it ever ran, its `Continue` button would raise `KeyError: 'final'` inside `nav`, because `SCREENS` has no `final` key.

---

## 3. IMAGE / ASSET MAP

### Screen → image mapping (every reference in the repository)

| Screen | Config key | Filename | Exact path | Exists? | Used by function |
|---|---|---|---|---|---|
| gate | `gate` | `gate.jpg` | `assets/gate.jpg` | ✅ | `show` → `render` → `photo_for` |
| welcome | `welcome` | `welcome.jpg` | `assets/welcome.jpg` | ✅ | `show` → `render` → `photo_for` |
| how | `how` | `how.jpg` | `assets/how.jpg` | ✅ | `show` → `render` → `photo_for` |
| tech | `tech` | `tech.jpg` | `assets/tech.jpg` | ✅ | `show` → `render` → `photo_for` |
| ai | `ai` | `ai.jpg` | `assets/ai.jpg` | ✅ | `show` → `render` → `photo_for` |
| results | `welcome` | `welcome.jpg` | `assets/welcome.jpg` | ✅ but **key unused** | `results` sends text; the key is never read |
| results album | `REVIEWS[0..4]` | `reviews1-5.jpg` | `assets/reviews1.jpg` … `reviews5.jpg` | ✅ ×5 | `results` → `photo_for` → `send_media_group` |
| access | `access` (video) | `access.mp4` | `assets/access.mp4` | ✅ | `show` → `render(is_video=True)` → `video_for` |
| register | `register` | `register.jpg` | `assets/register.jpg` | ✅ | `show` → `render` → `photo_for` |
| howto | `howto` | `howto.jpg` | `assets/howto.jpg` | ❌ **MISSING** | `show` → `render` (text fallback) |
| menu | `menu` | `menu.jpg` | `assets/menu.jpg` | ✅ | `_show_menu` → `render` → `photo_for` |
| mode | `trading_mode` | `trading_mode.jpg` | `assets/trading_mode.jpg` | ✅ | `show` → `render` → `photo_for` |
| type | `trading_type` | `trading_type.jpg` | `assets/trading_type.jpg` | ✅ | `show` → `render` → `photo_for` |
| asset | `asset_category` | `asset_category.jpg` | `assets/asset_category.jpg` | ✅ | `show` → `render` → `photo_for` |
| pairs | `currency_pair` | `currency_pair.jpg` | `assets/currency_pair.jpg` | ✅ | `show_pairs` → `render` → `photo_for` |
| test_menu | `expiration_time` | `expiration_time.jpg` | `assets/expiration_time.jpg` | ✅ | `show` → `render` → `photo_for` |
| analyzing | *(none)* | *(edits the previous message)* | — | n/a | `_run_signal` → `_edit_signal` |
| BUY result | `buy` (literal in `bot.py:291`) | `buy.jpg` | `assets/buy.jpg` | ❌ **MISSING** | `render` (text fallback) |
| gallery *(dead)* | `REVIEWS[i]` | `reviews*.jpg` | `assets/reviews*.jpg` | ✅ | `gallery` — unreachable |
| final *(dead)* | — | — | — | ❌ no `SCREENS["final"]` | `cb:go:final` would `KeyError` |

**Missing referenced assets: `howto.jpg`, `buy.jpg`.** (The `asset` / `pairs` / `test_menu` screens were repointed at `asset_category.jpg` / `currency_pair.jpg` / `expiration_time.jpg`, which all exist.)

### Physical inventory of `assets/`

| Filename | Ext | Size (B) | Dimensions | Git-tracked | Referenced by code |
|---|---|---|---|---|---|
| `access.mp4` | .mp4 | 40,822,989 | n/a (video) | ✅ | ✅ `access` screen |
| `asset.jpg.jpg` | .jpg | 247,701 | 1280×720 | ❌ **untracked** | ❌ **no** — malformed name |
| `final.jpg` | .jpg | 238,877 | 1672×941 | ✅ | ❌ **no** — orphan |
| `gate.jpg` | .jpg | 140,250 | 1280×720 | ✅ | ✅ |
| `how.jpg` | .jpg | 247,701 | 1280×720 | ✅ | ✅ |
| `how3.jpg` | .jpg | 196,302 | 1280×720 | ✅ | ❌ **no** — orphan |
| `howto.jpg` | .jpg | 112,249 | 1280×720 | ✅ | ✅ |
| `menu.jpg` | .jpg | 366,267 | 1600×914 | ✅ | ✅ |
| `mode.jpg` | .jpg | 238,877 | 1672×941 | ✅ | ✅ |
| `register.jpg` | .jpg | 257,818 | 1280×720 | ✅ | ✅ |
| `reviews1.jpg` | .jpg | 97,544 | 592×1280 | ✅ | ✅ |
| `reviews2.jpg` | .jpg | 96,523 | 587×1280 | ✅ | ✅ |
| `reviews3.jpg` | .jpg | 93,875 | 586×1280 | ✅ | ✅ |
| `reviews4.jpg` | .jpg | 95,952 | 590×1280 | ✅ | ✅ |
| `reviews5.jpg` | .jpg | 108,258 | 590×1280 | ✅ | ✅ |
| `success.jpg` | .jpg | 191,830 | 1280×731 | ✅ | ❌ **no** — orphan |
| `support.jpg` | .jpg | 273,344 | 1280×731 | ✅ | ❌ **no** — orphan |
| `tech.jpg` | .jpg | 247,701 | 1280×720 | ✅ | ✅ |
| `type.jpg` | .jpg | 238,877 | 1672×941 | ✅ | ✅ |
| `welcome.jpg` | .jpg | 277,141 | 1672×941 | ✅ | ✅ |

### ⚠️ Double-extension files

**`assets/asset.jpg.jpg` — present, untracked, unreferenced.**

`photo_for` builds `assets/<key>.jpg`, so the `asset` screen looks for `assets/asset.jpg` and will **never** find `asset.jpg.jpg`. This file is dead weight until renamed. It is also **byte-identical to `how.jpg` and `tech.jpg`** (MD5 `6C110A83A68A…`), so it is a duplicate of an existing screen image, not new artwork.

A second instance of the same mistake, `assets/welcome.jpg.jpg`, existed earlier and was resolved in commit `619853a` by moving it onto `welcome.jpg`. **This pattern has now occurred twice** — something in the image-saving workflow appends `.jpg` to a name that already carries it. No `*.png.png` or `*.jpeg.jpeg` files exist.

### Duplicate image content (by MD5)

| MD5 (prefix) | Files |
|---|---|
| `6C110A83A68A` | `how.jpg`, `tech.jpg`, `asset.jpg.jpg` |
| `0C6CCA1F4E53` | `final.jpg`, `mode.jpg`, `type.jpg` |

**`how.jpg` and `tech.jpg` being identical is a live visual bug** — consecutive funnel screens show the same picture. See §9.

---

## 4. BUTTON / CALLBACK MAP

### Router topology

There is exactly **one** router: the module-level `dp = Dispatcher()` (`bot.py:15`). No `Router()` objects, no `include_router` calls. Every handler is attached by a decorator at import time, so **registration order is literal source order in `bot.py`**, and aiogram dispatches to the *first* matching filter. Specific `==` handlers must therefore precede their `startswith` siblings — a constraint the code documents at `bot.py:213-214`, `220`, `226`, `232`.

### Registration order

| # | Line | Handler | Filter |
|---|---|---|---|
| 1 | 151 | `start` | `message(CommandStart())` |
| 2 | 157 | `check_sub` | `data == "check_sub"` |
| 3 | 165 | `results` | `data == "results"` |
| 4 | 196 | `nav` | `data.startswith("go:")` |
| 5 | 215 | `menu_signal` | `data == "menu:signal"` |
| 6 | 221 | `mode_manual` | `data == "mode:manual"` |
| 7 | 227 | `type_otc` | `data == "type:otc"` |
| 8 | 240 | `asset_forex` | `data == "asset:forex"` |
| 9 | 248 | `pairs_page` | `data.startswith("pairpage:")` |
| 10 | 258 | `pair_action` | `data.startswith("pair:")` |
| 11 | 268 | `s_action` | `data.startswith("s:")` |
| 12 | 355 | `m_action` | `data.startswith("m:")` |
| 13 | 363 | `new_signal` | `data == "new_signal"` |
| 14 | 371 | `asset_action` | `data.startswith("asset:")` |
| 15 | 377 | `type_action` | `data.startswith("type:")` |
| 16 | 383 | `mode_action` | `data.startswith("mode:")` |
| 17 | 389 | `menu_action` | `data.startswith("menu:")` |
| 18 | 395 | `gallery` | `data.startswith("gallery:")` |
| 19 | 405 | `noop` | `data == "noop"` |
| 20 | 457 | `capture_uid` | `message(Reg.waiting_uid)` |

All five specific-before-generic pairs are correctly ordered (5<17, 6<16, 7<15, 8<14, 9<10). **No handler is shadowed.** `pairpage:3` would not match `startswith("pair:")` in any case — the fifth character is `p`, not `:` — but `pairs_page` is registered first to match the convention used everywhere else in the file.

### Every inline button

| Label | callback_data | Handler | Order | After clicking |
|---|---|---|---|---|
| Subscribe to Channel | *(URL)* | — | — | Opens `CHANNEL_URL` |
| Check Subscription | `check_sub` | `check_sub` | 2 | Verified → `welcome`; else alert |
| Start | `go:how` | `nav` | 4 | → `how` |
| How Does It Work | `go:tech` | `nav` | 4 | → `tech` |
| See the technology | `go:ai` | `nav` | 4 | → `ai` (text fallback) |
| See real results | `results` | `results` | 3 | Album + results text |
| Get access to Go+ | `go:access` | `nav` | 4 | → `access` (video) |
| Open Telegram channel | *(URL)* | — | — | Opens `CHANNEL_URL` |
| Activate Bot | `go:register` | `nav` | 4 | → `register`, sets `Reg.waiting_uid`, arms nudge |
| 🔑 Register & Get Access | *(URL)* | — | — | Opens `REF_LINK` |
| 👥 How to register | `go:howto` | `nav` | 4 | → `howto` |
| 🙋 Support | *(URL)* | — | — | Opens `SUPPORT_URL` |
| Back to registration | `go:register` | `nav` | 4 | → `register` |
| 🚀 Get a signal | `menu:signal` | `menu_signal` | 5 | → `mode` |
| 🌲 My level | `menu:level` | `menu_action` | 17 | Alert "Coming soon 🚀" |
| 🧑 Support / VIP team / Pocket Option / ✈️ Telegram channel / ▶️ YouTube channel | *(URL)* | — | — | Open respective links |
| ✋ Manual | `mode:manual` | `mode_manual` | 6 | → `type` |
| 🔓 Automatic | `mode:auto` | `mode_action` | 16 | Alert "Coming soon 🚀" |
| « Back *(mode)* | `go:menu` | `nav` | 4 | → `menu` |
| 🔹 OTC | `type:otc` | `type_otc` | 7 | → `asset` |
| 🔒 FIN | `type:fin` | `type_action` | 15 | Alert "Coming soon 🚀" |
| « Back *(type)* | `menu:signal` | `menu_signal` | 5 | → `mode` |
| 🔄 Currency pairs | `asset:forex` | `asset_forex` | 8 | → `pairs` |
| 🔒 Cryptocurrencies / Stocks / Indices / Commodities | `asset:crypto`/`stocks`/`indices`/`commodities` | `asset_action` | 14 | Alert "Coming soon 🚀" |
| « Back *(asset)* | `mode:manual` | `mode_manual` | 6 | → `type` |
| 57 OTC pairs, 6 per page | `pair:audcad` … `pair:kesusd` | `pair_action` | 10 | Records pair → `test_menu` |
| `N/10` | `noop` | `noop` | 19 | Nothing — inert page indicator |
| `›` | `pairpage:<next>` | `pairs_page` | 9 | Next page, wrapping 10 → 1 |
| « Back *(pairs)* | `type:otc` | `type_otc` | 7 | → `asset` |
| 🔒 S5…S55 (9) | `s:5` … `s:55` | `s_action` | 11 | Alert "🔒 Locked. Coming soon" |
| 🔥 M1 … ✅ M10 (10) | `m:1` … `m:10` | `m_action` | 12 | Starts countdown → signal |
| 🚀 New Signal | `new_signal` | `new_signal` | 13 | Re-runs the signal with the last expiration |
| Continue *(dead)* | `go:final` | `nav` | 4 | ⚠️ Would raise `KeyError: 'final'` |
| ◀️ / ▶️ *(dead)* | `gallery:*` | `gallery` | 18 | Unreachable |

**`cb.answer()` audit:** every callback handler calls it — and in all except `check_sub` (where it carries a message) it is the *first* statement. No button can leave a spinner hanging.

### Non-standard button fields

`build_kb` passes `style` and `icon_custom_emoji_id` to `InlineKeyboardButton` (`bot.py:73-76`). Neither is a documented Telegram Bot API field for inline keyboard buttons; they survive because aiogram's models permit extra fields. This is pre-existing behaviour across all screens, but it is a compatibility risk worth knowing about when upgrading aiogram: if Telegram or aiogram ever rejects the unknown fields, **every screen** fails at once, not just one.

---

## 5. CONFIGURATION (`config.py`)

### Env-backed settings (lines 4-29)

| Name | Env var | Default | Notes |
|---|---|---|---|
| `CHANNEL_ID` | `CHANNEL_ID` | `@apextraderrr` | Cast to `int` when numeric (supports `-100…` ids) |
| `CHANNEL_URL` | `CHANNEL_URL` | `https://t.me/apextraderrr` | |
| `REF_LINK` | `REF_LINK` | `https://example.com/PLACEHOLDER_REF` | ⚠️ **placeholder** |
| `SUPPORT` / `SUPPORT_URL` | `SUPPORT` | `@PLACEHOLDER_SUPPORT` | ⚠️ **placeholder**; URL derived by stripping `@` |
| `VIP_LINK` | `VIP_LINK` | `https://t.me/PLACEHOLDER_VIP` | ⚠️ **placeholder** |
| `YOUTUBE_URL` | `YOUTUBE_URL` | `https://youtube.com/PLACEHOLDER_YT` | ⚠️ **placeholder** |
| `ADMIN_IDS` | `ADMIN_IDS` | `[]` | Parsed list of ints — **never used anywhere** |
| `MIN_DEPOSIT` | `MIN_DEPOSIT` | `Decimal("50")` | |
| `CAMPAIGN_ID` | `CAMPAIGN_ID` | `"969716"` | |
| `VERIFY_MODE` / `TEST_MODE` | `VERIFY_MODE` | `live` | `test` **bypasses all verification** |

The comment at lines 7-9 records a real past outage: link defaults must stay syntactically valid URLs, because Telegram rejects an entire message if any inline button URL is malformed — "this is what broke the menu after verification". `build_kb`'s `_URL_OK` guard (`bot.py:61`) is the defence-in-depth for that.

### Premium emoji layer (lines 31-75)

`E_*` constants are Telegram custom-emoji IDs. `pe(emoji_id, fallback)` wraps a fallback glyph in `<tg-emoji emoji-id="…">…</tg-emoji>`; the `T_*` constants are pre-rendered `pe()` calls used inside caption text. In keyboards the raw `E_*` id is passed as the 4th tuple element (`icon_custom_emoji_id`). Two code comments (`config.py:139-141`, `150-151`) warn that an **invalid** custom-emoji ID makes Telegram reject the whole message, which is why some screens deliberately use plain unicode instead of `pe()`.

### `SCREENS` (lines 77-215)

A dict of 15 screens. Each entry: `"photo"` **or** `"video"` (the media key), `"text"` (HTML caption), `"kb"` (rows of `(label, action, style?, icon?)` tuples).

Keys: `gate`, `welcome`, `how`, `tech`, `ai`, `results`, `access`, `register`, `menu`, `mode`, `type`, `asset`, `pairs`, `test_menu`, `howto`. **There is no `final`, `success`, `support`, or `buy` screen** — though `buy` is used as a media key directly from `bot.py:291`, and `success.jpg`/`support.jpg`/`final.jpg` sit unused in `assets/`.

Actions use a 3-char prefix convention: `cb:` → callback (stripped by `build_kb`), `url:` → link button.

### Remaining constants (lines 217-267)

`REVIEWS` (album keys) · `SIGNAL_COUNTDOWN=30`, `SIGNAL_STEP=5` · `SIGNAL_ANALYZING` (only `{timer}` may vary between edits — Telegram rejects an unchanged edit) · `SIGNAL_RESULT` (BUY hardcoded) · `SIGNAL_KB` · `DEFAULT_PAIR` · `PAIRS`/`PAIR_CODES`/`PAIR_PAGES`/`pairs_kb()` · `DAILY_SIGNAL_LIMIT=30`, `MSG_DAILY_LIMIT`, `LIMIT_KB` · `REGISTER_NUDGE` · verdict messages `MSG_NEED_DEPOSIT`, `MSG_WRONG_LINK`, `MSG_TEST_MODE`, `MSG_DELAYED`, `MSG_UID_ERROR`.

---

## 6. DATABASE

- **Type:** PostgreSQL, accessed with **`asyncpg`** (raw SQL — no ORM, no models/dataclasses).
- **Connection:** `db.py:5` reads `os.environ["DATABASE_URL"]` and rewrites a `postgresql+asyncpg://` prefix to `postgresql://` (asyncpg rejects the SQLAlchemy-style scheme). **Required at import time — a missing value crashes the process on startup.**
- **Pool:** `asyncpg.create_pool(min_size=1, max_size=5)` in `connect()`, called first thing in `main()`. The schema is applied on every boot via `CREATE TABLE IF NOT EXISTS` + `ADD COLUMN IF NOT EXISTS` (`db.py:8-40`) — there is **no migration tool**.

### `users`

| Column | Type | Meaning |
|---|---|---|
| `tg_id` | `BIGINT PRIMARY KEY` | Telegram user id |
| `username` | `TEXT` | Telegram @username |
| `uid` | `TEXT UNIQUE` | Pocket Option account id supplied by the user |
| `verified` | `BOOLEAN DEFAULT FALSE` | Passed campaign + deposit check |
| `deposit` | `NUMERIC(12,2) DEFAULT 0` | Deposit recorded at verification |
| `attempts` | `INT DEFAULT 0` | **Declared but never read or written** |
| `ui_msg_id` | `BIGINT` | Message id of the current on-screen UI message |
| `last_checked` | `TIMESTAMPTZ` | Last verification timestamp |
| `created_at` | `TIMESTAMPTZ DEFAULT now()` | |
| `album_ids` | `TEXT` | Added via `ALTER`; CSV of review-album message ids — **only ever set to `NULL`** |
| `signals_used_today` | `INT NOT NULL DEFAULT 0` | Added via `ALTER`; signals delivered to this user on `last_reset_date` |
| `last_reset_date` | `DATE` | Added via `ALTER`; the day `signals_used_today` counts. Not today ⇒ treated as 0 and rewritten on next read/write |

### `traders`

| Column | Type | Meaning |
|---|---|---|
| `trader_id` | `TEXT PRIMARY KEY` | Affiliate trader id |
| `registered` | `BOOLEAN DEFAULT TRUE` | **Never explicitly written** |
| `deposit` | `NUMERIC(12,2) DEFAULT 0` | Additive via postbacks, absolute via panel snapshots |
| `last_event` | `TEXT` | Postback event name, or `panel:campaign=<id>` |
| `updated_at` | `TIMESTAMPTZ DEFAULT now()` | |

### `postbacks`

| Column | Type | Meaning |
|---|---|---|
| `id` | `BIGSERIAL PRIMARY KEY` | |
| `raw` | `JSONB` | Raw postback payload, logged **before** any parsing so unknown macros are recoverable |
| `created_at` | `TIMESTAMPTZ DEFAULT now()` | |

### Personal data stored

Telegram user id, Telegram username, the user's Pocket Option account id, deposit amount, verification flag/timestamp, and transient message ids. Postback payloads may contain affiliate identifiers. No passwords, no payment instruments, no message content.

### Query helpers

`connect`, `touch_user`, `get_user`, `set_ui_msg`, `set_album`, `uid_owner`, `save_uid_only`, `log_postback`, `upsert_trader`, `get_trader`, `cache_trader`, `set_verified`, `unverified_with_uid`. Note `get_trader` is defined but never called.

Documented debt (`db.py:60-61`): `users.uid` is a stopgap; UID + verification state should move to a dedicated table with postback linkage.

---

## 7. DEPLOYMENT

- **Platform:** Railway (referenced throughout the code comments). The custom domain is routed to `$PORT`.
- **Config in repo:** `Procfile` only. There is **no** `railway.json`, `railway.toml`, `nixpacks.toml`, or `Dockerfile` — the build is inferred by Railway's Python builder from `requirements.txt` + `.python-version` (3.12).
- **Startup command:** `web: python bot.py`
- **Branch:** `main`, pushed to `origin` (`github.com/neetud252-create/funnel-bot`). A push to `main` triggers a redeploy.

### Process model (`bot.py:490-506`)

```
db.connect()
Bot(os.environ["BOT_TOKEN"])
bot.delete_webhook(drop_pending_updates=True)
panelbot.start()                       # degrades to disabled if creds absent
asyncio.gather(uvicorn server.serve(), dp.start_polling(bot), retry_worker(bot))
```

- **Update mode: long polling.** `delete_webhook(drop_pending_updates=True)` runs at every boot — so any tap made during a redeploy is **discarded**, and no webhook is ever registered.
- **HTTP:** uvicorn binds `0.0.0.0:$PORT` (default 8000) serving `server.app` — `/`, `/health`, and `/postback/{secret}`.
- **Background:** `retry_worker` re-checks unverified users with a UID every 30 minutes and pushes the menu when one passes.
- **Logging:** `logging.basicConfig(level=logging.INFO)` → stdout → Railway logs. Notable log lines: `asset '<key>' missing …` (ERROR), `dropping button … invalid URL` (WARNING), `VERIFY_MODE=test …` (WARNING), `sub check failed`, `panelbot connected as …`.

### ⚠️ Deployment-behaviour risks

1. `asyncio.gather(...)` is called **without `return_exceptions=True`**. If uvicorn fails to bind `$PORT`, or polling dies, the exception propagates, `main()` exits, the process dies, and Railway restarts it — a crash loop in which **every** button is dead.
2. `os.environ["BOT_TOKEN"]` (`bot.py:492`), `os.environ["DATABASE_URL"]` (`db.py:5`) and `os.environ["POSTBACK_SECRET"]` (`server.py:51`) use bracket access. The first two crash at startup if unset; the third raises per-request (returning 500 to the affiliate system).
3. Long polling permits **only one** live instance. Two overlapping deployments or replicas cause Telegram `409 Conflict` and updates split unpredictably between them — the classic "buttons randomly stopped working" symptom.

### Required environment variables (names only — no values)

**Mandatory:** `BOT_TOKEN`, `DATABASE_URL`, `POSTBACK_SECRET`
**Provided by Railway:** `PORT`
**Funnel links / targeting:** `CHANNEL_ID`, `CHANNEL_URL`, `REF_LINK`, `SUPPORT`, `VIP_LINK`, `YOUTUBE_URL`, `ADMIN_IDS`
**Verification:** `VERIFY_MODE`, `CAMPAIGN_ID`, `MIN_DEPOSIT`
**Panel (Telethon):** `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELETHON_SESSION`, `PANEL_BOT`

All except the mandatory three have in-code defaults. `TELEGRAM_*`/`TELETHON_SESSION` being unset simply disables verification with a warning.

---

## 8. GIT HISTORY

### Latest 20 commits

| Hash | Date | Message |
|---|---|---|
| `619853a` | 2026-08-10 | Update welcome screen image |
| `d92571a` | 2026-08-10 | Update images |
| `546d6c7` | 2026-08-09 | tapping an M button runs the signal analysis flow |
| `4fd0ae3` | 2026-08-06 | picking a currency pair opens the test menu |
| `5b5212c` | 2026-08-06 | "Currency pairs" opens the pair selector |
| `56413b2` | 2026-08-06 | "OTC" opens the asset-category screen |
| `0a16512` | 2026-08-05 | "Manual" opens the market-type screen |
| `af94502` | 2026-08-05 | add assets/mode.jpg so the mode screen works in prod |
| `f8015be` | 2026-08-05 | "Get a signal" opens the trading-mode screen |
| `8a010b7` | 2026-08-05 | fix: a UID can never leave the user with silence |
| `64161ea` | 2026-08-05 | menu screen: 30-signal counters, "Coming soon" popup emoji |
| `1edcb5d` | 2026-08-05 | VERIFY_MODE toggle to bypass panel verification in testing |
| `65517bd` | 2026-08-05 | post-verification main menu screen |
| `86806db` | 2026-08-01 | Group F: panel-bot verification via Telethon user session |
| `7c8536a` | 2026-08-01 | add missing assets/howto.jpg so the howto screen deploys |
| `be443b7` | 2026-08-01 | Group C: FastAPI postback receiver alongside polling |
| `0827853` | 2026-08-01 | register screen: delayed follow-up nudge |
| `6e3e55f` | 2026-08-01 | interim register flow: UID capture after the access screen |
| `a1c2b7b` | 2026-08-01 | add missing assets/access.mp4 so the access screen deploys |
| `5b23717` | 2026-08-01 | add video-backed access screen after results |

### Commits by area

**`bot.py` (handlers)** — `546d6c7`, `4fd0ae3`, `5b5212c`, `56413b2`, `0a16512`, `af94502`, `f8015be`, `8a010b7`, `64161ea`, `1edcb5d`, `65517bd`, `86806db`, `be443b7`, `0827853`, `6e3e55f`, `5b23717`, `3e1a534`, `ed1db8c`, `9457c13`, `d41e3d1`

**`config.py`** — `546d6c7`, `4fd0ae3`, `5b5212c`, `56413b2`, `0a16512`, `f8015be`, `8a010b7`, `64161ea`, `1edcb5d`, `65517bd`, `86806db`, `0827853`, `6e3e55f`, `5b23717`, `09e8658`, `8ba1c40`, `702da7c`, `3ea6915`, `d339e71`, `0ec3565`

**`assets/`** — `619853a`, `d92571a`, `0a16512`, `af94502`, `65517bd`, `7c8536a`, `a1c2b7b`, `702da7c`, `26d14e1`, `d671f46`, `4bffc5c`, `4be2b0c`

**Database (`db.py`)** — `86806db`, `be443b7`, `6e3e55f`, `5fdd5d7`, `f72fce9`, `b6cca2f`, `3d0660b`, `f5c1ced`, `c31242f`, `f4c7919`, `3eac7df`, `95ca734`

**`server.py` / `panelbot.py`** — `8a010b7`, `86806db`, `be443b7`

**Deployment config** (`Procfile`, `requirements.txt`, `.python-version`, `.gitignore`) — `8a010b7`, `86806db`, `be443b7`, `1c8a3f5`, `a982f25`, `70195be`, `79c1d5a`

**Note on the two most recent commits:** `d92571a` and `619853a` changed **image files only — zero lines of Python.** The last commit touching any `.py` file is `546d6c7` (2026-08-09).

---

## 9. CURRENT STATUS

### Completed

- Subscription gate with real `get_chat_member` verification
- Full pre-registration funnel: gate → welcome → how → tech → ai → results (5-photo album) → access (video)
- Register screen with FSM UID capture, uniqueness check, `\d{5,15}` validation, and a delayed nudge
- Group C: FastAPI postback receiver with raw-payload logging and best-effort macro parsing
- Group F: Telethon panel-bot verification with locking, spacing, hard timeout and graceful degradation
- Verification verdicts (verified / low deposit / wrong campaign / panel down) + 30-minute retry worker
- Post-verification menu, mode → type → asset → pairs → test-menu navigation
- Signal countdown engine: in-place edits, cancellation of a competing run, ownership check before the final swap
- Robustness: URL validation before Telegram sees a button, text-only media fallback, catch-all around UID capture

### Unfinished / placeholder

- **Signal engine** — direction is hardcoded to BUY (`config.py:230`); no market data anywhere
- **Automatic mode**, **FIN market**, **crypto/stocks/indices/commodities**, **all S expirations**, **My level** — all "Coming soon" alerts
- **Pairs pagination** — ✅ done: 57 pairs over 10 pages, `PAIR_PAGES` derived from `len(config.PAIRS)`, `›` pages forward and wraps. Still forward-only (no `‹`), because adding a third button would change the button layout
- **Menu counters** — ✅ the signal counters are live per-user state (see *Daily signal quota* below). **"Level: Start" is still static text.**
- **`howto` screen** — stub text: "Step-by-step registration guide coming here."
- **Placeholder links** — `REF_LINK`, `SUPPORT`, `VIP_LINK`, `YOUTUBE_URL` all still default to `PLACEHOLDER` values
- **`_pair_choice` / `_expiry_choice`** — in-memory only; a restart mid-funnel loses the user's pair

### TODOs in source

| Location | TODO |
|---|---|
| `config.py:10-11` | Swap the three placeholder links for real ones |
| `config.py:13` | Real support handle (was `@go_plus_supportbot`) |
| `config.py:25-27` | **`VERIFY_MODE` must be set back to `live` before real users** |
| `config.py:123-125` | Wire menu counters/level to per-user state |
| `config.py:141` | Swap in `pe(E_SPEECH, …)` once a real emoji id exists |
| `config.py` (`asset` / `test_menu` screens) | `asset.jpg` / `test_menu.jpg` do not exist yet (the pair screen now uses `currency_pair.jpg`, which does) |
| `config.py:193` | S35/S40 intentionally absent |
| `config.py:209` | Replace `howto` stub with the real guide |
| `config.py:230` | Direction hardcoded to BUY |
| `config.py:238-239` | Picked pair lives in memory only |
| `bot.py:113` | Register flow is interim pending Group C verification |
| `bot.py:332, 338, 344, 350` | Locked categories / FIN / auto mode / level logic |
| `bot.py:455-457` | Testing bypass — set `VERIFY_MODE` back to `live` |
| `db.py:60-61` | Move UID to a dedicated table with postback linkage |
| `server.py:8-11` | Postback macro names are guesses pending a real payload |

### Known bugs and live issues

1. ~~`assets/ai.jpg` is missing~~ — **fixed**; the file is present again.
2. ~~`how.jpg` and `tech.jpg` are byte-identical~~ — **fixed**; every asset now has a distinct MD5.
3. **Two missing assets** — `howto.jpg` and `buy.jpg`. The `howto` stub and the final signal-result screen fall back to text-only.
4. **`cb:go:final` would crash** — `bot.py:361` emits it but `SCREENS` has no `final` key → `KeyError` inside `nav`, *after* `cb.answer()` has already fired, i.e. a silently dead button. Currently unreachable because nothing emits `cb:gallery:*`.
5. **Review albums are never cleaned up** — `wipe()` reads `album_ids`, but `results` never stores the album's message ids.
6. **`VERIFY_MODE` bypass** — if left at `test`, *any* numeric UID gets full access with no campaign or deposit check.
7. **`asyncio.gather` without `return_exceptions=True`** — one subsystem failure kills the whole process (see §7).
8. **`trim.py` is a live hazard** — it rewrites `config.py` in place, deleting the `howto`, `support`, `register` and `final` screens and rewriting `"photo": "final"` → `"photo": "welcome"`. It targets screens that no longer exist in their referenced form. Running it today would **destroy the register and howto screens**. It appears to be the origin of the inert `"photo": "welcome"` on the `results` screen.

### Suspicious / stale references

- `SCREENS["results"]["photo"] = "welcome"` — never read by the `results` handler; a `trim.py` artefact
- `attempts` and `registered` columns — declared, never used
- `album_ids` — only ever cleared, never populated
- `db.get_trader` — defined, never called
- `httpx`, `apscheduler` — pinned, never imported
- `ADMIN_IDS` — parsed, never used
- `gallery` handler + `cb:go:final` — dead code
- `E_INFO`, `E_BACK`, `E_STAR`… several emoji constants are unused

### Unused assets (tracked, referenced by nothing)

`6055311397081518489_121.jpg` (and `access.mov`, the source of `access.mp4`).

### Missing assets (referenced, not on disk)

`howto.jpg`, `buy.jpg`

---

## 10. CURRENT IMAGE / SCREENSHOT MAPPING

Derived strictly from `photo_for` (`bot.py:31`) + `media_missing` (`bot.py:41`) against the files actually present on disk.

| Screen | Image file displayed **right now** |
|---|---|
| Subscription gate | `assets/gate.jpg` |
| **First welcome screen** | **`assets/welcome.jpg`** (277,141 B, 1672×941, MD5 `D444F31E…` — the new image) |
| Why traders choose Go+ (`how`) | `assets/how.jpg` — ⚠️ identical bytes to `tech.jpg` |
| **"How Does It Work" destination** (`tech`) | **`assets/tech.jpg`** — ⚠️ identical bytes to `how.jpg`, so the screen looks unchanged |
| AI technology (`ai`) | ❌ **no image** — `assets/ai.jpg` missing → text-only |
| Results | ❌ **no single image** — a 5-photo album (`reviews1-5.jpg`) followed by a plain text message. The `"photo": "welcome"` key is never read. |
| Access | `assets/access.mp4` (video) |
| Register | `assets/register.jpg` |
| How to register (`howto`) | `assets/howto.jpg` |
| Main menu | `assets/menu.jpg` |
| Trading mode | `assets/mode.jpg` |
| Market type | `assets/type.jpg` |
| **Asset category** | ❌ **no image** — `assets/asset.jpg` missing → text-only (`asset.jpg.jpg` is *not* picked up) |
| **Currency pair** | ✅ `assets/currency_pair.jpg`, 57 OTC pairs across 10 pages |
| **Testing menu** | ❌ **no image** — `assets/test_menu.jpg` missing → text-only |
| **Analyzing screen** | ❌ **no image** — edits the test-menu message in place; since that message is text, `edit_message_text` is used |
| **BUY result screen** | ❌ **no image** — `assets/buy.jpg` missing → text-only |
| Gallery *(dead code)* | would use `reviews*.jpg` |

**Summary: 6 of the funnel's screens currently render with no image at all**, and the two remaining pre-registration screens (`how`, `tech`) show the same picture as each other.

---

*Generated by a read-only audit at commit `619853a`. No source file was modified; `assets/asset.jpg.jpg` was deliberately left untouched.*
