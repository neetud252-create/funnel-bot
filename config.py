import os
from decimal import Decimal

_ch = os.getenv("CHANNEL_ID", "@apextraderrr")
CHANNEL_ID  = int(_ch) if _ch.lstrip("-").isdigit() else _ch
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/apextraderrr")
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
        "text": pe("5276032951342088188", "\U0001F4A5") + " <b>This is where it starts.</b>\n\n" + pe("5287684458881756303", "\U0001F916") + " <b>Go Plus shows you what it sees \U00002014 you decide what to do.</b>\n\n" + pe("5244837092042750681", "\U0001F4C8") + " <b>Simple as that.</b>\n\n" + pe("5352825278672412291", "\U00002705") + " <b>Activate the bot now and explore the signals for yourself \U0001F447</b>",
        # Interim: routes into the UID-capture register flow (Group C adds full verification)
        "kb": [[("Activate Bot", "cb:go:register", "success", "6280525956771745921")]],
    },
    "register": {
        "photo": "register",
        "text": pe("5465198403573012261", "\U0001F510") + " <b>To access Go+, register for a new Pocket Option account using my link:</b>\n\n\U0001F449 " + REF_LINK + "\n\U00002E3B\n\n" + pe("5352825278672412291", "\U00002705") + " <b>Once you register, send your new account ID in the text box below</b> \U0001F447\n\n" + pe("5447644880824181073", "\U000026A0\U0000FE0F") + " <b>Please note:</b> Your ID must contain <b>numbers only</b> \U00002014 no extra symbols \U00002757\n\nExample: <b>123456789</b>",
        "kb": [[("\U0001F511 Register & Get Access", "url:" + REF_LINK, "success", "5307843983102204243")],
               [("\U0001F465 How to register", "cb:go:howto", "primary")],
               [("\U0001F64B Support", "url:" + SUPPORT_URL)]],
    },
    # Post-verification home screen (shown once a UID passes the campaign +
    # deposit check). TODO: the signal counters and level are static until the
    # signal engine lands - wire them to per-user state then.
    "menu": {
        "photo": "menu",
        "text": T_ROBOT + " <b>Go+ main menu</b>\n\n" + T_BOLT + " <b>Signals</b>\nAvailable today: 30 signals\nUsed: 0\nLeft: 30\n\n" + T_STAR + " <b>Your level:</b> Start",
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
        "photo": "mode",
        "text": "\U0001F4AC <b>Select trading mode:</b>\n\n" + T_N1 + " <b>Manual</b> \U00002014 you choose asset &amp; time\n" + T_N2 + " <b>Automatic</b> \U00002014 bot chooses everything\n\n" + T_DOWN + " <b>Choose below</b>",
        "kb": [[("\U0000270B Manual", "cb:mode:manual", "success")],
               [("\U0001F513 Automatic", "cb:mode:auto", "primary")],
               [("\U000000AB Back", "cb:go:menu")]],
    },
    # Market-type picker, opened from "Manual" on the mode screen. Both types are
    # still placeholders - see type_action in bot.py. Speech emoji is plain
    # unicode for the same reason as the mode screen above.
    "type": {
        "photo": "type",
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
        "photo": "asset",
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
    # the speech emoji above. Pairs are placeholders - see pair_action in bot.py.
    # TODO: the "1/10" indicator and "›" are inert (cb:noop) until real pagination
    # lands; the page count is not derived from anything yet.
    # TODO: assets/pairs.jpg does not exist yet - until it is added, render()
    # falls back to sending this screen as text with its keyboard intact.
    "pairs": {
        "photo": "pairs",
        "text": "\U0001F500 <b>Select a currency pair:</b>\n\n" + T_DOWN + " <b>Choose below</b>",
        "kb": [[("AUD/CAD OTC", "cb:pair:audcad"), ("AUD/CHF OTC", "cb:pair:audchf")],
               [("AUD/NZD OTC", "cb:pair:audnzd"), ("AUD/USD OTC", "cb:pair:audusd")],
               [("CAD/CHF OTC", "cb:pair:cadchf"), ("CHF/JPY OTC", "cb:pair:chfjpy")],
               [("1/10", "cb:noop"), ("\U0000203A", "cb:noop")],
               [("\U000000AB Back", "cb:type:otc")]],
    },
    # Shown after a currency pair is picked. S buttons are locked, M buttons are
    # placeholders - see s_action / m_action in bot.py. All emoji here are plain
    # unicode by request, no pe().
    # TODO: S35 and S40 are intentionally absent - the rows jump 30 -> 45.
    # TODO: assets/test_menu.jpg does not exist yet - until it is added, render()
    # falls back to sending this screen as text with its keyboard intact.
    "test_menu": {
        "photo": "test_menu",
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
# the analyzing countdown, then replaces it with the finished signal. All emoji
# here are plain unicode by request, no pe(). See _run_signal in bot.py.
SIGNAL_COUNTDOWN = 30   # seconds shown on the timer, counted down to 00:00
SIGNAL_STEP = 5         # seconds between edits of the same message

# Only {timer} may change between edits - Telegram rejects an edit whose text is
# identical to what is already on screen.
SIGNAL_ANALYZING = ("\U0001F4CA \U0001F50D I'm analyzing the chart. It won't take long.\n"
                    "\U0000231B Please wait {timer} \U00002014 the signal is almost there.")

# TODO: the direction is hardcoded to BUY until the signal engine lands.
SIGNAL_RESULT = ("\U00002705 The analysis is complete!\n\n"
                 "\U0001F4B1 Currency pair: {pair}\n"
                 "\U000023F1\U0000FE0F Expiration time: {expiry}\n"
                 "\U0001F514 Signal: BUY \U0001F7E2\U0001F7E2")

SIGNAL_KB = [[("\U0001F680 New Signal", "cb:new_signal", "success")]]

# TODO: the picked pair only lives in memory (bot.py _pair_choice), so a restart
# mid-funnel falls back to this label.
DEFAULT_PAIR = "AUD/CAD OTC"

# Delayed follow-up sent a few seconds after the register screen opens (bot.py).
REGISTER_NUDGE = pe("5420323339723881652", "\U000026A0\U0000FE0F") + " <b>Don't leave your account half-done.</b>\n\nRegistering takes one minute. Your ID unlocks everything.\n\n" + pe("5188481279963715781", "\U0001F680") + " <b>Finish now \U00002014 send your account ID below.</b>"

# --- Group F verification verdict messages (pe() style, factual, no scarcity) ---
_MINDEP = str(int(MIN_DEPOSIT)) if MIN_DEPOSIT == MIN_DEPOSIT.to_integral_value() else str(MIN_DEPOSIT)

# Access granted: campaign matches and deposits meet the minimum -> the "menu"
# screen above is shown instead of a text verdict.

# Campaign matches but deposits are below the minimum.
MSG_NEED_DEPOSIT = T_MONEY + " <b>Almost there.</b>\n\nYour account is registered through our link. To unlock access, top up your balance with <b>$" + _MINDEP + "</b> or more \U00002014 verification then completes automatically."

# Account not found, or registered under a different campaign.
MSG_WRONG_LINK = pe("5447644880824181073", "\U000026A0\U0000FE0F") + " <b>Account not linked to us.</b>\n\nTo get access, your Pocket Option account must be created through our official link. Please register with the button below, then send your new account ID."

# Prepended to the menu screen when TEST_MODE is on, so a bypassed verification
# is never mistaken for a real one.
MSG_TEST_MODE = pe("5447644880824181073", "\U000026A0\U0000FE0F") + " <b>TEST MODE \U00002014 verification bypassed, real check is OFF</b>"

# Panel bot didn't answer in time; the retry worker will keep checking.
MSG_DELAYED = T_CLOCK + " <b>Verification is taking a moment.</b>\n\nWe're still checking your account. You'll be notified here automatically as soon as it's confirmed \U00002014 or you can send your account ID again shortly."

# Last-resort reply when the UID handler itself fails (see capture_uid). Plain
# text, no buttons, so it can't fail for the same reason the handler did.
MSG_UID_ERROR = pe("5447644880824181073", "\U000026A0\U0000FE0F") + " <b>Something went wrong on our side.</b>\n\nYour account ID wasn't processed. Please send it again in a moment \U00002014 if it keeps failing, contact support: " + SUPPORT
