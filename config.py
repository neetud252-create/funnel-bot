import os
from decimal import Decimal

_ch = os.getenv("CHANNEL_ID", "@apexxtraderz")
CHANNEL_ID  = int(_ch) if _ch.lstrip("-").isdigit() else _ch
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/apexxtraderz")
# Link defaults MUST stay valid http(s) URLs even when unset: Telegram rejects
# the whole message if any inline button URL is malformed, which takes down the
# entire screen (this is what broke the menu after verification).
# TODO: swap the three PLACEHOLDER links below for the real ones (set REF_LINK,
# SUPPORT, VIP_LINK, YOUTUBE_URL in the Railway service variables).
REF_LINK    = os.getenv("REF_LINK", "https://example.com/PLACEHOLDER_REF")
SUPPORT     = os.getenv("SUPPORT", "@PLACEHOLDER_SUPPORT")   # TODO: real support handle (was @go_plus_supportbot)
SUPPORT_URL = "https://t.me/" + SUPPORT.lstrip("@")
VIP_LINK    = os.getenv("VIP_LINK", "https://t.me/PLACEHOLDER_VIP")          # TODO: real VIP team invite
YOUTUBE_URL = os.getenv("YOUTUBE_URL", "https://youtube.com/PLACEHOLDER_YT")  # TODO: real YouTube channel
ADMIN_IDS   = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

# Group F verification thresholds.
MIN_DEPOSIT = Decimal(os.getenv("MIN_DEPOSIT", "50"))
CAMPAIGN_ID = os.getenv("CAMPAIGN_ID", "969716")

# Verification mode. "live" (default) runs the real @AffiliatePocketBot lookup.
# "test" skips the panel entirely and verifies any numeric UID on the spot.
# TODO: VERIFY_MODE MUST be set back to "live" (or unset) before real users
# reach the bot - in test mode ANY account ID gets full access with no check
# against the campaign or the deposit minimum.
VERIFY_MODE = os.getenv("VERIFY_MODE", "live").strip().lower()
TEST_MODE = VERIFY_MODE == "test"

E_INFO  = "5334544901428229844"
E_POINT = "5415758949129404605"
E_BACK  = "5305522282695768654"
E_MONEY = "5224257782013769471"
E_FLASH = "5823347218056221496"
E_STAR  = "5463297803235113601"
E_GEM   = "5427168083074628963"
E_QMARK = "5436113877181941026"
E_ROBOT = "5287684458881756303"
E_GREEN = "5188234920639632382"
E_DOWN  = "5447183459602669338"
E_SHAKE = "5357122032674818130"
E_GEAR  = "5341715473882955310"
E_N1    = "5778373820930858379"
E_N2    = "5778382698628256004"
E_N3    = "5778338052443213984"
E_N4    = "5778346006722646362"
E_N5    = "5778205144680239810"
E_CLOCK = "5258095024725321202"
E_CHART = "5231200819986047254"
E_LENS  = "5348544647977254780"
E_BOLT  = "5895638385300606573"

def pe(emoji_id, fallback):
    return '<tg-emoji emoji-id="' + emoji_id + '">' + fallback + '</tg-emoji>'

T_INFO  = pe(E_INFO, "\u2139\uFE0F")
T_POINT = pe(E_POINT, "\U0001F449")
T_MONEY = pe(E_MONEY, "\U0001F4B0")
T_STAR  = pe(E_STAR, "\u2728")
T_GEM   = pe(E_GEM, "\U0001F48E")
T_ROBOT = pe(E_ROBOT, "\U0001F916")
T_GREEN = pe(E_GREEN, "\U0001F7E2")
T_DOWN  = pe(E_DOWN, "\U0001F53D")
T_SHAKE = pe(E_SHAKE, "\U0001F91D")
T_GEAR  = pe(E_GEAR, "\u2699\uFE0F")
T_N1    = pe(E_N1, "1\uFE0F\u20E3")
T_N2    = pe(E_N2, "2\uFE0F\u20E3")
T_N3    = pe(E_N3, "3\uFE0F\u20E3")
T_N4    = pe(E_N4, "4\uFE0F\u20E3")
T_N5    = pe(E_N5, "5\uFE0F\u20E3")
T_CLOCK = pe(E_CLOCK, "\u23F0")
T_CHART = pe(E_CHART, "\U0001F4CA")
T_LENS  = pe(E_LENS, "\U0001F50D")
T_BOLT  = pe(E_BOLT, "\u26A1")

# --- Currency pairs ---------------------------------------------------------
# Single source of truth for the OTC pair list: the keyboard, the pagination,
# the callback codes and the label shown on the signal screen are all derived
# from PAIRS below. Appending here is the only edit needed to add a pair.
# Order is preserved (the first six are the original ones, so page 1 looks
# exactly as it did) and duplicates are dropped by _dedupe_pairs.
PAIRS_RAW = [
    "AUD/CAD OTC", "AUD/CHF OTC", "AUD/NZD OTC", "AUD/USD OTC",
    "CAD/CHF OTC", "CHF/JPY OTC", "EUR/GBP OTC", "EUR/HUF OTC",
    "EUR/NZD OTC", "EUR/TRY OTC", "EUR/USD OTC", "GBP/AUD OTC",
    "GBP/JPY OTC", "JOD/CNY OTC", "MAD/USD OTC", "NZD/JPY OTC",
    "TND/USD OTC", "USD/ARS OTC", "USD/BRL OTC", "USD/CAD OTC",
    "USD/CNH OTC", "USD/DZD OTC", "USD/INR OTC", "USD/MXN OTC",
    "USD/SGD OTC", "USD/VND OTC", "EUR/JPY OTC", "USD/BDT OTC",
    "USD/PKR OTC", "BHD/CNY OTC", "ZAR/USD OTC", "USD/COP OTC",
    "USD/THB OTC", "USD/IDR OTC", "CAD/JPY OTC", "OMR/CNY OTC",
    "CHF/NOK OTC", "NZD/USD OTC", "USD/PHP OTC", "AED/CNY OTC",
    "QAR/CNY OTC", "USD/JPY OTC", "EUR/RUB OTC", "NGN/USD OTC",
    "AUD/JPY OTC", "USD/CLP OTC", "USD/CHF OTC", "UAH/USD OTC",
    "GBP/USD OTC", "USD/EGP OTC", "YER/USD OTC", "SAR/CNY OTC",
    "USD/MYR OTC", "LBP/USD OTC", "USD/RUB OTC", "EUR/CHF OTC",
    "KES/USD OTC",
]

def pair_code(label):
    # "AUD/CAD OTC" -> "audcad". callback_data is capped at 64 bytes and has to
    # stay stable across restarts, so it is derived from the pair itself rather
    # than from a list index (which would shift when a pair is inserted).
    base = str(label).upper().replace("OTC", "")
    return "".join(ch for ch in base if ch.isalnum()).lower()

def _dedupe_pairs(labels):
    # Keyed on the code, so "eur/usd otc" and "EUR/USD  OTC" collapse into one
    # button instead of two that behave identically.
    seen, out = set(), []
    for raw in labels:
        label = " ".join(str(raw).split())
        code = pair_code(label)
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(label)
    return out

PAIRS = _dedupe_pairs(PAIRS_RAW)
# code -> label, for naming the pair on the signal screen after a tap.
PAIR_CODES = {pair_code(p): p for p in PAIRS}

# Grid is unchanged from the original screen: 2 buttons per row, 3 rows of
# pairs, then the page indicator row and Back.
PAIRS_PER_ROW  = 2
PAIR_ROWS      = 3
PAIRS_PER_PAGE = PAIRS_PER_ROW * PAIR_ROWS
# Recalculated from PAIRS on import - never hardcode the page count again.
PAIR_PAGES = max(1, -(-len(PAIRS) // PAIRS_PER_PAGE))

def pairs_kb(page=0):
    # Keyboard for one page. The page number wraps in both directions, so an
    # out-of-range value from a stale button can't produce an empty screen.
    page = page % PAIR_PAGES
    chunk = PAIRS[page * PAIRS_PER_PAGE:(page + 1) * PAIRS_PER_PAGE]
    rows = [[(label, "cb:pair:" + pair_code(label))
             for label in chunk[i:i + PAIRS_PER_ROW]]
            for i in range(0, len(chunk), PAIRS_PER_ROW)]
    # Same two-button pagination row as before - the indicator stays inert and
    # "\u203A" now advances, wrapping past the last page back to the first, so
    # every pair is reachable without adding a button to the layout.
    rows.append([("%d/%d" % (page + 1, PAIR_PAGES), "cb:noop"),
                 ("\U0000203A", "cb:pairpage:%d" % ((page + 1) % PAIR_PAGES))])
    rows.append([("\U000000AB Back", "cb:type:otc")])
    return rows

SCREENS = {
    "gate": {
        "photo": "gate",
        "text": "To continue, subscribe to the best Telegram channel about trading:\n\n" + T_POINT + " " + CHANNEL_URL + "\n\n\U0001F464 Once subscribed, click the \u201cCheck Subscription\u201d button below \U0001F447",
        "kb": [[("Subscribe to Channel", "url:" + CHANNEL_URL, "primary", E_POINT)],
               [("Check Subscription", "cb:check_sub", "success", E_INFO)]],
    },
    "welcome": {
        "photo": "welcome",
        "text": "\U0001F590\uFE0F <b>Hello! I am Go+, your personal trading bot.</b>\n\n" + T_MONEY + " I help you approach trading with clear, data-driven insights - without stress and without complex analysis.",
        "kb": [[("Start", "cb:go:how", "success", E_FLASH)]],
    },
    "how": {
        "photo": "how",
        "text": T_STAR + "<b>Why traders choose Go+:</b>\n\n100+ trading assets\nOTC and exchange trading\n2 trading modes for every style\nInstant chart analysis\nAvailable 24/7 and compatible with any device\n\n" + T_GEM + " <b>You get a tool that is always one step ahead of the market.</b>",
        "kb": [[("How Does It Work", "cb:go:tech", "primary", E_QMARK)]],
    },
    "tech": {
        "photo": "tech",
        "text": T_ROBOT + " <b>It is simple:</b>\n\n" + T_N1 + " Select an asset\n" + T_N2 + " Choose the expiration time\n" + T_N3 + " Get a signal: BUY " + T_GREEN + " / SELL " + T_DOWN + "\n" + T_N4 + " " + T_CLOCK + " Open a trade\n" + T_N5 + " Track the result\n\n" + T_SHAKE + " I take care of the market analysis for you - all you have to do is act.",
        "kb": [[("See the technology", "cb:go:ai", "primary", E_GEAR)]],
    },
    "ai": {
        "photo": "ai",
        "text": T_GEAR + "<b>I am powered by advanced AI,</b>\nwhich processes huge amounts of data in seconds.\n\n" + T_CHART + " <b>I analyze charts using hundreds of indicators, price patterns, and technical analysis tools.</b>\n\n" + T_LENS + " <b>I detect patterns that humans often miss.</b>\n\n" + T_BOLT + " <b>Every signal I generate is based on precise calculations - not guesswork.</b>",
        "kb": [[("See real results", "cb:results", "primary")]],
    },
    "results": {
        "photo": "welcome",
        "text": "<b>Real feedback from active Go+ traders.</b>\n\n" + pe("5370740716840425754", "\u261d\ufe0f") + " The screenshots above are just a tiny fraction of the results.\n\n\U0001F49F More feedback is published on our trading channel:\n\n\U0001F449 " + CHANNEL_URL,
        "kb": [[("Get access to Go+", "cb:go:access", "success", "5307843983102204243")],
               [("Open Telegram channel", "url:" + CHANNEL_URL, "primary", "5220069871072583573")]],
    },
    "access": {
        "video": "access",
        "text": "\U0001F4A5 <b>I made $18,400 in an instant!</b>\n\nMany people try to learn sophisticated trading strategies to be able to buy themselves a brand new car, a rolex and a new house. I just follow the instructions from Go Plus and lock in profits right away.\n\n\U0001F916 <b>Go Plus tells you what to do \U00002014 and you just do what it tells you.</b>\n\n\U0001F4C8 <b>It couldn't be easier than that.</b>\n\n\U00002705 <b>Activate the bot right now and follow your dream \U0001F447</b>",
        # Interim: routes into the UID-capture register flow (Group C adds full verification)
        "kb": [[("Activate Bot", "cb:go:register", "success", "6280525956771745921")]],
    },
    "register": {
        "photo": "register",
        "text": "\U0001F510 To access Go+, register for a new Pocket Option account using my link:\n\n\U0001F449 https://shorturl.at/2fu2t\n\n\U00002705 Once you register, send your new account ID in the text box below \U0001F447\n\n\U000026A0\U0000FE0F Please note: Your ID must contain numbers only \U00002014 no extra symbols \U00002757\n\nExample: 123456789",
        "kb": [[("\U0001F511 Register & Get Access", "url:https://shorturl.at/2fu2t", "success", "5307843983102204243")],
               [("\U0001F465 How to register", "url:https://youtu.be/uJHBwXZVnNI?si=bhC7oMFLvoJfiQy", "primary")],
               [("\U0001F64B Support", "url:" + SUPPORT_URL)]],
    },
    # Post-verification home screen (shown once a UID passes the campaign +
    # deposit check). TODO: the signal counters and level are static until the
    # signal engine lands - wire them to per-user state then.
    "menu": {
        "photo": "menu",
        "text": "\U0001F916 <b>Go+ main menu</b>\n\n\U0001F514 <b>Signals</b>\n\U00002014 Available today: 30 signals\n\U00002014 Used: 0\n\U00002014 Left: 30\n\n\U0001FAAB <b>Your level:</b> Start",
        "kb": [[("\U0001F680 Get a signal", "cb:menu:signal", "success")],
               [("\U0001F332 My level", "cb:menu:level", "primary")],
               [("\U0001F9D1 Support", "url:" + SUPPORT_URL)],
               [("VIP team", "url:" + VIP_LINK)],
               [("Pocket Option", "url:" + REF_LINK)],
               [("\U00002708\U0000FE0F Telegram channel", "url:" + CHANNEL_URL)],
               [("\U000025B6\U0000FE0F YouTube channel", "url:" + YOUTUBE_URL)]],
    },
    # Trading-mode picker, opened from "Get a signal" on the menu. Both modes are
    # still placeholders - see mode_action in bot.py.
    # NOTE: the speech emoji is plain unicode, not pe() - we have no verified
    # custom emoji ID for it, and an invalid ID makes Telegram reject the whole
    # message. Swap in pe(E_SPEECH, ...) once a real ID is on hand.
    "mode": {
        "photo": "trading_mode",
        "text": "\U0001F4AC <b>Select trading mode:</b>\n\n" + T_N1 + " <b>Manual</b> \U00002014 you choose asset &amp; time\n" + T_N2 + " <b>Automatic</b> \U00002014 bot chooses everything\n\n" + T_DOWN + " <b>Choose below</b>",
        "kb": [[("\U0000270B Manual", "cb:mode:manual", "success")],
               [("\U0001F513 Automatic", "cb:mode:auto", "primary")],
               [("\U000000AB Back", "cb:go:menu")]],
    },
    # Market-type picker, opened from "Manual" on the mode screen. Both types are
    # still placeholders - see type_action in bot.py. Speech emoji is plain
    # unicode for the same reason as the mode screen above.
    "type": {
        "photo": "trading_type",
        "text": "\U0001F4AC <b>Select market type:</b>\n\n" + T_N1 + " <b>OTC</b> \U00002014 available even on weekends\n" + T_N2 + " <b>FIN</b> \U00002014 real exchange market prices\n\n" + T_DOWN + " <b>Choose below</b>",
        "kb": [[("\U0001F539 OTC", "cb:type:otc", "success")],
               [("\U0001F512 FIN", "cb:type:fin", "primary")],
               [("\U000000AB Back", "cb:menu:signal")]],
    },
    # Asset-category picker, opened from "OTC" on the market-type screen. All five
    # categories are still placeholders - see asset_action in bot.py. Speech emoji
    # is plain unicode for the same reason as the mode screen above.
    # TODO: assets/asset.jpg does not exist yet - until it is added, render()
    # falls back to sending this screen as text with its keyboard intact.
    "asset": {
        "photo": "asset_category",
        "text": "\U0001F4AC <b>Select asset category:</b>\n\n" + T_N1 + " <b>Currency pairs</b> \U00002014 classic Forex\n" + T_N2 + " <b>Cryptocurrencies</b> \U00002014 high volatility\n" + T_N3 + " <b>Stocks</b> \U00002014 global companies\n" + T_N4 + " <b>Indices</b> \U00002014 market trends\n" + T_N5 + " <b>Commodities</b> \U00002014 gold, oil &amp; more\n\n" + T_DOWN + " <b>Choose below</b>",
        "kb": [[("\U0001F504 Currency pairs", "cb:asset:forex", "success")],
               [("\U0001F512 Cryptocurrencies", "cb:asset:crypto")],
               [("\U0001F512 Stocks", "cb:asset:stocks")],
               [("\U0001F512 Indices", "cb:asset:indices")],
               [("\U0001F512 Commodities", "cb:asset:commodities")],
               [("\U000000AB Back", "cb:mode:manual")]],
    },
    # Currency-pair picker, opened from "Currency pairs" on the asset screen.
    # Shuffle emoji is plain unicode - no verified custom emoji ID for it, same as
    # the speech emoji above.
    # The keyboard is page 1 only - what show() renders for a plain "pairs"
    # screen. Paging goes through show_pairs() in bot.py, which calls the same
    # pairs_kb() builder, so the two can't drift apart.
    "pairs": {
        "photo": "currency_pair",
        "text": "\U0001F500 <b>Select a currency pair:</b>\n\n" + T_DOWN + " <b>Choose below</b>",
        "kb": pairs_kb(0),
    },
    # Shown after a currency pair is picked. S buttons are locked, M buttons are
    # placeholders - see s_action / m_action in bot.py. All emoji here are plain
    # unicode by request, no pe().
    # TODO: S35 and S40 are intentionally absent - the rows jump 30 -> 45.
    # TODO: assets/test_menu.jpg does not exist yet - until it is added, render()
    # falls back to sending this screen as text with its keyboard intact.
    "test_menu": {
        "photo": "expiration_time",
        "text": "\U0001F680 <b>Upcoming Project Test</b>\nWe're preparing something new and exciting.\nBe early. Be involved. Help us build better.\n\n\U00002B07️ <b>Choose below</b>",
        "kb": [[("\U0001F512 S5", "cb:s:5")],
               [("\U0001F512 S10", "cb:s:10"), ("\U0001F512 S15", "cb:s:15")],
               [("\U0001F512 S20", "cb:s:20"), ("\U0001F512 S25", "cb:s:25"), ("\U0001F512 S30", "cb:s:30")],
               [("\U0001F512 S45", "cb:s:45"), ("\U0001F512 S50", "cb:s:50"), ("\U0001F512 S55", "cb:s:55")],
               [("\U0001F525 M1", "cb:m:1")],
               [("M2", "cb:m:2"), ("M3", "cb:m:3")],
               [("M4", "cb:m:4"), ("M5", "cb:m:5"), ("M6", "cb:m:6")],
               [("M7", "cb:m:7"), ("M8", "cb:m:8"), ("M9", "cb:m:9")],
               [("\U00002705 M10", "cb:m:10")]],
    },
    # TODO(Group C): replace stub with the real step-by-step registration guide
    "howto": {
        "photo": "howto",
        "text": "Step-by-step registration guide coming here.",
        "kb": [[("Back to registration", "cb:go:register", "primary")]],
    },
}

REVIEWS = ["reviews1", "reviews2", "reviews3", "reviews4", "reviews5"]

# --- Signal flow: an unlocked M button on the test menu edits that screen into
# a single static "analyzing" notice, then replaces it with the finished signal
# once the selected wait has elapsed. All emoji here are plain unicode by
# request, no pe(). See _run_signal in bot.py.
SIGNAL_COUNTDOWN = 30   # fallback wait (seconds) when the expiration won't parse

# Shown once, right after the M button is tapped, and never edited again - there
# is no live timer. {wait} is the human label for the delay ("1 minute",
# "10 minutes").
SIGNAL_ANALYZING = ("\U0001F4CA Analyzing the market...\n\n"
                    "Your signal will be delivered in approximately {wait}.")

# TODO: the direction is picked at random per signal (see _run_signal in bot.py)
# until the real signal engine lands - it is not derived from any market data.
SIGNAL_RESULT = ("\U00002705 The analysis is complete!\n\n"
                 "\U0001F4B1 Currency pair: {pair}\n"
                 "\U000023F1\U0000FE0F Expiration time: {expiry}\n"
                 "\U0001F514 Signal: {direction}")

# One of these is chosen uniformly at random for every signal. BUY pairs with UP
# and SELL pairs with DOWN on purpose: drawing the two halves independently
# would produce contradictory signals like "SELL ... UP".
SIGNAL_DIRECTIONS = ("BUY \U0001F7E2\U0001F7E2 UP \U00002B06\U0000FE0F",
                     "SELL \U0001F534\U0001F534 DOWN \U00002B07\U0000FE0F")

SIGNAL_KB = [[("\U0001F680 New Signal", "cb:new_signal", "success")]]

# TODO: the picked pair only lives in memory (bot.py _pair_choice), so a restart
# mid-funnel falls back to this label.
DEFAULT_PAIR = "AUD/CAD OTC"

# Delayed follow-up sent a few seconds after the register screen opens (bot.py).
REGISTER_NUDGE = "\U00002757 Only 2 Go+ activations left today.\n\nNo extensions. No second chance.\n\n\U0001F680 Activate Go+ now."

# --- Group F verification verdict messages (pe() style, factual, no scarcity) ---
_MINDEP = str(int(MIN_DEPOSIT)) if MIN_DEPOSIT == MIN_DEPOSIT.to_integral_value() else str(MIN_DEPOSIT)

# Access granted: campaign matches and deposits meet the minimum -> the "menu"
# screen above is shown instead of a text verdict.

# Campaign matches but deposits are below the minimum.
MSG_NEED_DEPOSIT = T_MONEY + " <b>Almost there.</b>\n\nYour account is registered through our link. To unlock access, top up your balance with <b>$" + _MINDEP + "</b> or more \U00002014 verification then completes automatically."

# Account not found, or registered under a different campaign.
MSG_WRONG_LINK = pe("5447644880824181073", "\U000026A0\U0000FE0F") + " <b>Account not linked to us.</b>\n\nTo get access, your Pocket Option account must be created through our official link. Please register with the button below, then send your new account ID."

# Prepended to the menu screen when TEST_MODE is on. Blanked so the banner no
# longer shows; kept defined because bot.py still references it. NOTE: this only
# hides the notice - VERIFY_MODE=test still bypasses verification entirely.
MSG_TEST_MODE = ""

# Panel bot didn't answer in time; the retry worker will keep checking.
MSG_DELAYED = T_CLOCK + " <b>Verification is taking a moment.</b>\n\nWe're still checking your account. You'll be notified here automatically as soon as it's confirmed \U00002014 or you can send your account ID again shortly."

# Last-resort reply when the UID handler itself fails (see capture_uid). Plain
# text, no buttons, so it can't fail for the same reason the handler did.
MSG_UID_ERROR = pe("5447644880824181073", "\U000026A0\U0000FE0F") + " <b>Something went wrong on our side.</b>\n\nYour account ID wasn't processed. Please send it again in a moment \U00002014 if it keeps failing, contact support: " + SUPPORT
