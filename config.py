import os
import re
from decimal import Decimal

_ch = os.getenv("CHANNEL_ID", "@apexxtraderz")
CHANNEL_ID  = int(_ch) if _ch.lstrip("-").isdigit() else _ch
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/apexxtraderz")

def _channel_mention(url):
    # "https://t.me/apexxtraderz" -> "@apexxtraderz", which Telegram auto-links
    # as a mention with no markup needed. Derived from CHANNEL_URL rather than
    # written out again, so the handle on the gate screen cannot drift from the
    # channel the Join button actually opens.
    #
    # Anything that is not a public t.me handle - a private invite link
    # (t.me/+hash), a joinchat URL, an override pointing elsewhere - falls back
    # to the raw URL. That is what this screen displayed before, and it still
    # auto-links, so an unusual CHANNEL_URL degrades instead of showing a
    # mention that does not resolve.
    m = re.match(r"https?://t\.me/([A-Za-z][A-Za-z0-9_]{3,31})/?$",
                 (url or "").strip())
    return "@" + m.group(1) if m else (url or "")

CHANNEL_MENTION = _channel_mention(CHANNEL_URL)
# Link defaults MUST stay valid http(s) URLs even when unset: Telegram rejects
# the whole message if any inline button URL is malformed, which takes down the
# entire screen (this is what broke the menu after verification).
# TODO: swap the three PLACEHOLDER links below for the real ones (set REF_LINK,
# SUPPORT, VIP_LINK, YOUTUBE_URL in the Railway service variables).
REF_LINK    = os.getenv("REF_LINK", "https://example.com/PLACEHOLDER_REF")
SUPPORT     = os.getenv("SUPPORT", "https://t.me/flashhher")   # TODO: real support handle (was @go_plus_supportbot)
SUPPORT_URL = "https://t.me/" + SUPPORT.lstrip("@")
VIP_LINK    = os.getenv("VIP_LINK", "https://t.me/PLACEHOLDER_VIP")          # TODO: real VIP team invite
YOUTUBE_URL = os.getenv("YOUTUBE_URL", "https://youtube.com/@pocketoption?si=gb2BpGjz2SzhMOH6s")  # TODO: real YouTube channel
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

# Background re-check of pending UIDs (bot.py retry_worker). OFF by default:
# after depositing, the user re-sends their account ID to trigger a fresh
# lookup, which is what MSG_NEED_DEPOSIT now instructs. Set ENABLE_AUTO_RETRY=1
# to restore the 30-minute sweep that used to complete verification on its own.
ENABLE_AUTO_RETRY = os.getenv("ENABLE_AUTO_RETRY", "").strip().lower() in ("1", "true", "yes", "on")

# Minimum seconds between panel lookups for ONE user. Lookups are serialised
# behind a lock with SPACING between them, so a handful of users re-sending
# account IDs can stack into a FloodWait that disables verification for
# everyone. This throttles per user before the shared queue is ever touched.
UID_LOOKUP_COOLDOWN = int(os.getenv("UID_LOOKUP_COOLDOWN", "20"))

# Chat the boot-time media warm sends throwaway uploads to (and deletes again).
# Telegram issues a file_id only in response to an actual send, so warming needs
# somewhere to send. Falls back to the first admin; unset means warming is
# skipped and the first user on each screen pays for that upload once.
_warm = os.getenv("MEDIA_WARM_CHAT", "").strip()
MEDIA_WARM_CHAT = (int(_warm) if _warm.lstrip("-").isdigit()
                   else (_warm or (ADMIN_IDS[0] if ADMIN_IDS else None)))

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
# Signal loading screen only. Deliberately separate IDs from E_CHART / E_LENS
# above: those belong to other screens and must not shift if this one is
# restyled.
E_SIG_CHART = "5451882707875276247"
E_SIG_LENS  = "5188217332748527444"
# Activation nudge (REGISTER_NUDGE) only. Swap these to change its emoji
# without touching the copy. Both are rendered through pe(), which embeds the
# plain-emoji fallback that clients without premium emoji show instead - an
# invalid ID makes Telegram reject the whole message, so keep the fallbacks.
E_NUDGE_WARN          = "5420323339723881652"
E_NUDGE_ROCKET        = "5188481279963715781"
NUDGE_WARN_FALLBACK   = "\U000026A0\U0000FE0F"   # warning sign
NUDGE_ROCKET_FALLBACK = "\U0001F680"             # rocket
E_SIG_GLASS = "5386367538735104399"
# Expiration-time screen (SCREENS["test_menu"]) only. Swap these to restyle it
# without touching the copy; each is rendered through pe(), which keeps the
# plain-emoji fallback that non-Premium clients see.
E_EXP_CLOCK = "5382194935057372936"
E_EXP_BULB  = "5258216851472654189"
E_EXP_DOWN  = "5406745015365943482"

# Main-menu and trading-mode MESSAGE emoji - the ones inside a screen's caption,
# as opposed to the button icons further down. These go through pe() into
# <tg-emoji> entities, so they render only on a message sent with
# parse_mode="HTML"; render() always does.
E_MENU_HEADER   = "5188678912883827293"   # the robot on "Go+ main menu"
E_MENU_SIGNALS  = "5429633836684157942"   # the bolt on the "Signals" line
E_MENU_LEVEL_HD = "5370688996844249600"   # the battery on "Your level:"
E_LEVEL_START   = "5274026806477857971"   # the star on the Start tier itself
E_MODE_MANUAL = "5258011929993026890"     # the person after "Manual"
E_MODE_AUTO   = "4943239162758169437"     # the star-struck face after "Automatic"
# Subscription gate (SCREENS["gate"]) only. The first four are CAPTION entities
# rendered through pe(); the last two are BUTTON icons and ride on the 4th
# tuple element (icon_custom_emoji_id), never inside the button label.
#
# NOTE: E_GATE_DOWN carries the same id as E_BACK above. It is declared
# separately rather than aliased, so restyling the Back arrow cannot silently
# change this screen, and vice versa.
E_GATE_LOCK  = "5296369303661067030"      # the padlock on the headline
E_GATE_MEGA  = "5983400750594658672"      # the megaphone before the handle
E_GATE_SOUND = "5247187233722607160"      # the speaker before the handle
E_GATE_DOWN  = "5305522282695768654"      # the finger pointing at the buttons
E_GATE_JOIN  = "5397916757333654639"      # Join Channel button icon
E_GATE_CHECK = "5260463209562776385"      # Check Subscription button icon
# Welcome screen (SCREENS["welcome"]) only. The first two are CAPTION entities
# rendered through pe(); E_WELCOME_START is the Start BUTTON icon and rides on
# the 4th tuple element, never inside the label.
#
# NOTE: two of these repeat ids already used elsewhere - E_WELCOME_DOWN matches
# E_BACK / E_GATE_DOWN, and E_WELCOME_START matches E_NUDGE_ROCKET and
# E_MENU_SIGNAL. Declared separately on purpose: restyling the menu's "Get a
# signal" icon or the Back arrow must not silently change this screen.
E_WELCOME_BOT   = "5924946082487341386"   # the robot on the headline
E_WELCOME_DOWN  = "5305522282695768654"   # the finger pointing at Start
E_WELCOME_START = "5188481279963715781"   # Start button icon
# "Why traders choose Go+" screen (SCREENS["how"]) only. All seven are CAPTION
# entities rendered through pe(); this screen's BUTTON icon is E_QMARK above,
# which already carries the required id and is used nowhere else.
#
# NOTE: three repeat ids used elsewhere - E_HOW_CHART matches E_CHART,
# E_HOW_HOUR matches E_SIG_GLASS, and E_HOW_DOWN matches E_BACK /
# E_GATE_DOWN / E_WELCOME_DOWN. Declared separately on purpose, so restyling
# any of those cannot silently change this screen.
E_HOW_SPARK  = "5325547803936572038"      # sparkles on the headline
E_HOW_CHART  = "5231200819986047254"      # bar chart, assets line
E_HOW_GLOBE  = "5447410659077661506"      # globe, OTC line
E_HOW_TARGET = "5461009483314517035"      # target, trading modes line
E_HOW_BOLT   = "5992366958681527437"      # bolt, chart analysis line
E_HOW_HOUR   = "5386367538735104399"      # hourglass, availability line
E_HOW_DOWN   = "5305522282695768654"      # the finger pointing at the button

# Main-menu button icons. Unlike the constants above these are NOT rendered
# through pe(): they go in the 4th slot of a button tuple, which build_kb passes
# as InlineKeyboardButton.icon_custom_emoji_id - the emoji Telegram draws before
# the button's label.
#
# TWO Bot API limits shape how these are used, and neither is ours to change:
#   * icon_custom_emoji_id is a single string. ONE custom emoji per button.
#   * InlineKeyboardButton.text is a plain label with no entities and no
#     parse_mode, so a custom emoji cannot be placed inside the text itself -
#     <tg-emoji> would render as literal markup. pe() is for message bodies only.
# Between them, a button can carry exactly one custom emoji, always leading.
E_MENU_SIGNAL   = "5188481279963715781"
E_MENU_LEVEL    = "5244837092042750681"
E_MENU_SUPPORT  = "5443038326535759644"
E_MENU_PREMIUM  = "5431684550424011313"
E_MENU_CHANNEL  = "5231489647946768652"
E_YOUTUBE       = "5897969921182142023"

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
T_SIG_CHART = pe(E_SIG_CHART, "\U0001F4CA")
T_SIG_LENS  = pe(E_SIG_LENS, "\U0001F50D")
# Fallback glyph is the spiral the reference shows. The entity ID is unchanged -
# it is still the one supplied for this slot - so premium clients see exactly
# what they saw before; only the non-premium fallback character moved.
T_SIG_GLASS = pe(E_SIG_GLASS, "\U0001F300")

# Main-menu caption and trading-mode screen. The fallback glyph inside each tag
# is what a client that cannot show custom emoji displays instead, so it is the
# emoji the entity depicts, not a stand-in.
T_MENU_HEADER   = pe(E_MENU_HEADER, "\U0001F916")      # robot
T_MENU_SIGNALS  = pe(E_MENU_SIGNALS, "\U000026A1")     # high voltage
T_MENU_LEVEL_HD = pe(E_MENU_LEVEL_HD, "\U0001FAAB")    # low battery
T_LEVEL_START   = pe(E_LEVEL_START, "\U00002B50")      # star
# E_CHART is reused deliberately: the supplied "Trading mode" id is byte for
# byte the one already defined for it, so this is the same entity, not a new one.
T_MODE_HEADER   = pe(E_CHART, "\U0001F4CA")            # bar chart
# The glyph inside a <tg-emoji> tag MUST be a single valid emoji: Telegram
# rejects the whole message with "Bad Request: ENTITY_TEXT_INVALID" otherwise,
# and an em dash is not an emoji. These two therefore carry the emoji their
# sticker actually depicts (confirmed via getCustomEmojiStickers): 👤 and 🤩.
T_MODE_MANUAL   = pe(E_MODE_MANUAL, "\U0001F464")      # bust in silhouette
T_MODE_AUTO     = pe(E_MODE_AUTO, "\U0001F929")        # star-struck

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
        # The handle is CHANNEL_MENTION, derived from CHANNEL_URL, so the text
        # a user reads and the channel the Join button opens are the same one.
        # No markup on it: Telegram auto-links a bare @handle as a mention.
        #
        # Button labels are bare words. Their emoji come from the 4th tuple
        # element, which build_kb passes as icon_custom_emoji_id - Telegram
        # draws that before the label, so a unicode emoji in the text too would
        # render a second glyph beside it. Styles, URL, callback and order are
        # unchanged from the previous version of this screen.
        "text": (pe(E_GATE_LOCK, "\U0001F512") + "ONE STEP TO UNLOCK GO+\n\n"
                 "Join our free trading channel to activate the bot:\n\n"
                 + pe(E_GATE_MEGA, "\U0001F4E3") + pe(E_GATE_SOUND, "\U0001F50A")
                 + CHANNEL_MENTION + "\n\n"
                 "Then tap Check Subscription below "
                 + pe(E_GATE_DOWN, "\U0001F447")),
        "kb": [[("Join Channel", "url:" + CHANNEL_URL, "primary", E_GATE_JOIN)],
               [("Check Subscription", "cb:check_sub", "success", E_GATE_CHECK)]],
    },
    "welcome": {
        "photo": "welcome",
        # Button label is the bare word "Start". Its rocket comes from the 4th
        # tuple element, which build_kb passes as icon_custom_emoji_id and
        # Telegram draws before the label - a unicode rocket in the text too
        # would render a second glyph beside it. Callback, style and order are
        # unchanged from the previous version of this screen.
        "text": (pe(E_WELCOME_BOT, "\U0001F916") + " Welcome to Go+\n\n"
                 "Your personal trading assistant \U00002014 clear, data-driven "
                 "signals without the complex analysis.\n\n"
                 + pe(E_WELCOME_DOWN, "\U0001F447") + " Tap Start to begin"),
        "kb": [[("Start", "cb:go:how", "success", E_WELCOME_START)]],
    },
    "how": {
        "photo": "how",
        # Spacing is deliberate and not a typo: the headline, the assets line
        # and the closing line put a space after their emoji, the four middle
        # feature lines do not. Keep it that way - it is what the supplied copy
        # specifies, and pe() emits the entity with no padding of its own.
        #
        # The button is unchanged: label already plain "How Does It Work" and
        # E_QMARK already carries the required id, so nothing here restyles it.
        "text": (pe(E_HOW_SPARK, "\U00002728") + " WHY TRADERS CHOOSE GO+\n\n"
                 + pe(E_HOW_CHART, "\U0001F4CA") + " 100+ trading assets\n"
                 + pe(E_HOW_GLOBE, "\U0001F310") + "OTC and exchange pairs\n"
                 + pe(E_HOW_TARGET, "\U0001F3AF") + "2 trading modes\n"
                 + pe(E_HOW_BOLT, "\U000026A1") + "Instant chart analysis\n"
                 + pe(E_HOW_HOUR, "\U0000231B") + "Available 24/7, any device\n\n"
                 + pe(E_HOW_DOWN, "\U0001F447") + " See how it works"),
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
    # deposit check). The signal counters are a format template - {limit},
    # {used} and {left} are filled per user by _show_menu in bot.py, which is
    # also why show() routes "menu" through it rather than rendering this text
    # directly (raw braces would leak onto the screen).
    # {level} is filled the same way, from the user's is_premium column, so the
    # level on this screen is per-user state rather than static text.
    "menu": {
        "photo": "menu",
        # Wording, line breaks and the three {placeholders} are unchanged; only
        # the three leading emoji became <tg-emoji> entities.
        "text": (T_MENU_HEADER + " <b>Go+ main menu</b>\n\n"
                 + T_MENU_SIGNALS + " <b>Signals</b>\n"
                 "\U00002014 Available today: {limit} signals\n"
                 "\U00002014 Used: {used}\n"
                 "\U00002014 Left: {left}\n\n"
                 + T_MENU_LEVEL_HD + " <b>Your level:</b> {level}"),
        # Every row carries style "primary", which the Bot API renders blue, so
        # the whole menu reads as one blue block instead of the mixed green /
        # blue / app-default it was. The only other styles Telegram accepts are
        # "success" (green) and "danger" (red); omitting it falls back to the
        # client's own default, which is what the unstyled rows used to do.
        # VIP_LINK and REF_LINK are still read above - REF_LINK is also used by
        # _register_btn() in bot.py, so it must not be removed with the button.
        # The leading unicode emoji are gone from the labels: each button now
        # carries its custom emoji in the 4th slot, and Telegram draws that
        # before the text, so keeping both would show two icons side by side.
        # That applies to "Unlock Premium" too - its single crown is the custom
        # emoji, which is why the label is bare text like all the others.
        # Every row carries style "primary", which the Bot API renders blue, so
        # the whole menu reads as one blue block. Telegram accepts exactly three
        # styles - "success" (green), "primary" (blue), "danger" (red) - and
        # omitting it falls back to the client's own default. Arbitrary colours
        # are not expressible here, so this is the full range available.
        "kb": [[("Get a signal", "cb:menu:signal", "primary", E_MENU_SIGNAL)],
               [("Unlock Premium", "cb:menu:premium", "primary", E_MENU_PREMIUM)],
               [("My level", "cb:menu:level", "primary", E_MENU_LEVEL)],
               [("Support", "url:" + SUPPORT_URL, "primary", E_MENU_SUPPORT)],
               [("Telegram channel", "url:" + CHANNEL_URL, "primary", E_MENU_CHANNEL)],
               [("YouTube channel", "url:" + YOUTUBE_URL, "primary", E_YOUTUBE)]],
    },
    # Trading-mode picker, opened from "Get a signal" on the menu. Both modes are
    # still placeholders - see mode_action in bot.py.
    # NOTE: the speech emoji is plain unicode, not pe() - we have no verified
    # custom emoji ID for it, and an invalid ID makes Telegram reject the whole
    # message. Swap in pe(E_SPEECH, ...) once a real ID is on hand.
    "mode": {
        "photo": "trading_mode",
        # T_N1 / T_N2 / T_DOWN already carried the supplied number, Automatic
        # and "Choose below" ids, so they are untouched. The header emoji is the
        # supplied Trading-mode entity (was a plain unicode speech balloon), and
        # the Manual / Automatic entities sit where the em dash used to, each
        # wrapping the emoji its own sticker depicts rather than the dash - a
        # dash there is what produced ENTITY_TEXT_INVALID and blanked the chat.
        "text": (T_MODE_HEADER + " <b>Select trading mode:</b>\n\n"
                 + T_N1 + " <b>Manual</b> " + T_MODE_MANUAL + " you choose asset &amp; time\n"
                 + T_N2 + " <b>Automatic</b> " + T_MODE_AUTO + " bot chooses everything\n\n"
                 + T_DOWN + " <b>Choose below</b>"),
        "kb": [[("Manual", "cb:mode:manual", "success", E_MODE_MANUAL)],
               [("Automatic", "cb:mode:auto", "primary", E_MODE_AUTO)],
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
    # The expiration screen, shown after a currency pair is picked. S buttons are
    # locked; every M button starts a signal - see s_action / m_action in bot.py.
    # All emoji here are plain unicode by request, no pe().
    # TODO: S35 and S40 are intentionally absent - the rows jump 30 -> 45.
    # There is no assets/test_menu.jpg and there never was: "test_menu" is the
    # screen key, and the image it renders is assets/expiration_time.jpg. This is
    # the only screen that shows it - tapping an M button here leads straight to
    # the waiting screen, which is text-only.
    "test_menu": {
        "photo": "expiration_time",
        # TODO: "Recommended: S5" is fixed copy - nothing computes it. Note that
        # every S button is locked by s_action, so S5 cannot actually be picked;
        # switch this to an M value or unlock S5 before it reads as truthful.
        "text": (pe(E_EXP_CLOCK, "\U000023F1") + " <b>Choose the expiration time:</b>\n\n"
                 + pe(E_EXP_BULB, "\U0001F4A1") + " <b>Recommended:</b> S5\n\n"
                 + pe(E_EXP_DOWN, "\U00002B07\U0000FE0F") + " Choose below"),
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

# --- Signal flow: an unlocked M button on the test menu tears down the tapped
# screen and puts up the waiting screen - two text messages, in this order
# (see _send_wait_screen in bot.py):
#   1. SIGNAL_CHART, the chart emoji alone in its own text message
#   2. SIGNAL_ANALYZING, the two-line analysis notice
# The analysis stage sends NO media at all - no photo, no album, no video. There
# is deliberately no waiting-image constant here to point one at.
# The chart is its own message rather than a caption, which is what puts it on
# its own line at full custom-emoji size - and a caption would require the photo
# this stage must not send.
# All three emoji are premium custom emoji, so these only render correctly with
# parse_mode="HTML" - <tg-emoji> is dropped otherwise, leaving the plain-unicode
# fallback that is written inside each tag. That fallback is what clients which
# cannot show custom emoji display; it is not a replacement for the entity.
# The result screen further down is still plain unicode.
# The one delay for every signal, in seconds, counted from the user's final
# selection. Deliberately independent of the chosen expiration: M1 and M10 both
# land in 30s, and the tapped M value survives only as the {expiry} label on the
# result screen below.
SIGNAL_COUNTDOWN = 30

# Message 1: the chart, alone. No text, no caption, no keyboard, no photo.
SIGNAL_CHART = T_SIG_CHART

# Message 2. Written once and never edited - there is no live timer, so {wait}
# is the full delay ("00:30" from _wait_label in bot.py), not a ticking value.
SIGNAL_ANALYZING = (T_SIG_LENS + " <b>I'm analyzing the chart. It won't take long.</b>\n\n"
                    + T_SIG_GLASS + " Please wait {wait} — the signal is almost there.")

# TODO: the direction is picked at random per signal (see _run_signal in bot.py)
# until the real signal engine lands - it is not derived from any market data.
SIGNAL_RESULT = ("\U00002705 The analysis is complete!\n\n"
                 "\U0001F4B1 Currency pair: {pair}\n"
                 "\U000023F1\U0000FE0F Expiration time: {expiry}\n"
                 "\U0001F514 Signal: {direction}")

# One of these is chosen uniformly at random for every signal. BUY pairs with UP
# and SELL pairs with DOWN on purpose: drawing the two halves independently
# would produce contradictory signals like "SELL ... UP".
# Each direction carries the artwork its result screen shows, so a direction can
# never be drawn without its matching image: assets/buy.jpg is the green BUY
# board, assets/sell.jpg the red SELL one.
SIGNAL_DIRECTIONS = (("BUY \U0001F7E2\U0001F7E2 UP \U00002B06\U0000FE0F", "buy"),
                     ("SELL \U0001F534\U0001F534 DOWN \U00002B07\U0000FE0F", "sell"))

SIGNAL_KB = [[("\U0001F680 New Signal", "cb:new_signal", "success")]]

# --- Levels and the daily signal quota --------------------------------------
# Per user, per day. Stored in users.signals_used_today / users.last_reset_date
# (see db.py), so a restart does not hand anyone a fresh allowance.
#
# Both tier limits come from Railway. The numbers below are only what applies
# when the variable is unset - no tier size is hardcoded in the limit logic:
# bot.py never names a number, it asks daily_limit(), and daily_limit() reads
# the two module globals below. Changing PREMIUM_DAILY_SIGNALS in Railway and
# restarting is therefore the whole procedure for changing the Premium cap.

def _int_env(name, default):
    # A typo'd variable falls back instead of taking the service down on boot -
    # an unparseable limit must not cost every user their signals. Negative
    # values are rejected for the same reason (they would read as "0 left").
    raw = (os.getenv(name) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 0 else default

LEVEL_START   = "start"
LEVEL_PREMIUM = "premium"

# START_DAILY_SIGNALS falls back to the legacy DAILY_SIGNAL_LIMIT before the
# built-in 30, so a service that still sets the old name keeps its value and
# nothing regresses at deploy time. The old variable can be deleted afterwards.
START_DAILY_SIGNALS   = _int_env("START_DAILY_SIGNALS", _int_env("DAILY_SIGNAL_LIMIT", 30))
PREMIUM_DAILY_SIGNALS = _int_env("PREMIUM_DAILY_SIGNALS", 70)

# Backward-compatible alias: anything still reading the old constant gets the
# Start limit, which is what it meant before Premium existed.
DAILY_SIGNAL_LIMIT = START_DAILY_SIGNALS

# Price of the Premium unlock, in the bot's own fictional tokens. Read from
# Railway like the two limits above, and passed to db.unlock_premium as the
# amount to deduct, so the number quoted on the locked screen and the number
# actually charged are always the same value. Not money: see the game_tokens
# comment in db.py.
PREMIUM_UNLOCK_COST = _int_env("PREMIUM_UNLOCK_COST", 100)

# Icon and name are kept apart because the two screens compose them
# differently: the menu caption wants "<star> Start" as one label, while the My
# level screen leads with the icon and ends with the name. The LEVEL_LABELS
# tables are derived rather than written out again, so a rename cannot leave the
# two screens disagreeing about what a tier is called.
#
# There are TWO icon tables on purpose, and they are not interchangeable.
#
# LEVEL_ICONS holds plain unicode. LEVEL_ICONS_TG wraps the same glyph in a
# <tg-emoji> entity where an ID has been supplied for that tier. An entity only
# renders on a message sent with parse_mode="HTML"; anywhere else the raw tag
# would be printed verbatim, and MSG_ADMIN_DONE is exactly such a place - the
# /premium confirmation goes out through m.answer() with no parse mode. So the
# screens use the *_tg helpers and the admin reply uses the plain ones.
#
# Only Start has a supplied ID. Premium keeps its plain trophy rather than an
# invented entity, which is why this table is built per tier instead of wrapping
# both blindly.
LEVEL_ICONS = {
    LEVEL_START:   "\U00002B50",
    LEVEL_PREMIUM: "\U0001F3C6",
}
LEVEL_ICONS_TG = {
    LEVEL_START:   T_LEVEL_START,
    LEVEL_PREMIUM: LEVEL_ICONS[LEVEL_PREMIUM],
}
LEVEL_NAMES = {
    LEVEL_START:   "Start",
    LEVEL_PREMIUM: "Premium",
}
LEVEL_LABELS    = {k: LEVEL_ICONS[k] + " " + LEVEL_NAMES[k] for k in LEVEL_NAMES}
LEVEL_LABELS_TG = {k: LEVEL_ICONS_TG[k] + " " + LEVEL_NAMES[k] for k in LEVEL_NAMES}

def level_of(is_premium):
    return LEVEL_PREMIUM if is_premium else LEVEL_START

def daily_limit(is_premium):
    # Reads the globals on every call rather than a precomputed table, so the
    # limit a user is held to always matches what this module currently holds.
    return PREMIUM_DAILY_SIGNALS if is_premium else START_DAILY_SIGNALS

def level_label(is_premium):
    # Plain unicode. Safe anywhere, including messages sent without parse_mode.
    return LEVEL_LABELS[level_of(is_premium)]

def level_label_tg(is_premium):
    # HTML only. Callers must send with parse_mode="HTML".
    return LEVEL_LABELS_TG[level_of(is_premium)]

def level_icon(is_premium):
    return LEVEL_ICONS[level_of(is_premium)]

def level_icon_tg(is_premium):
    return LEVEL_ICONS_TG[level_of(is_premium)]

def level_name(is_premium):
    return LEVEL_NAMES[level_of(is_premium)]

# "My level" screen, opened from the menu. Every field is filled per user by
# menu_level in bot.py - the tier and the limit come from the same lookup the
# enforcement uses, so this screen cannot advertise a number the server would
# refuse to honour.
# {tokens} is the in-game balance, appended rather than replacing the existing
# lines: the used/remaining counters are what the screen was already for.
MSG_LEVEL = ("{icon} <b>Your current level:</b> {name}\n"
             "\U0001F4CA <b>Daily limit:</b> {limit} signals\n"
             "\U0001F4C8 <b>Used today:</b> {used}\n"
             "\U000026A1 <b>Remaining today:</b> {left}\n"
             "\U0001FA99 <b>Game tokens:</b> {tokens}")

LEVEL_KB = [[("\U000000AB Back", "cb:go:menu")]]

# --- Unlock Premium screen --------------------------------------------------
# Opened from the main menu, where it replaced the VIP team link. Informational:
# it states the benefits, the configured Premium allowance and the viewer's own
# current status. {premium_limit} is PREMIUM_DAILY_SIGNALS, so the number quoted
# here is the same one the quota check enforces - it is never written out.
#
# There is deliberately no purchase flow here: Premium is assigned by an admin
# (/premium <tg_id>). Nothing on this screen asks for a deposit - the trading
# account balance gates verification, not the tier.
MSG_PREMIUM = ("\U0001F3C6 <b>Premium Level</b>\n\n"
               "<b>Premium benefits:</b>\n"
               "\U00002014 {premium_limit} signals/day\n"
               "\U00002014 Premium status\n\n"
               "{status}")

MSG_PREMIUM_ACTIVE = ("\U0001F3C6 <b>Your status:</b> Premium is active "
                      "\U00002014 {limit} signals/day.")

MSG_PREMIUM_INACTIVE = ("\U0001F7E2 <b>Your status:</b> Start "
                        "\U00002014 {limit} signals/day.\n\n"
                        "Premium is assigned by our team. Tap below to ask about it.")

def premium_kb(is_premium):
    # A user who already has Premium gets no request button - there is nothing
    # for them to ask for, and offering it would read as though it had lapsed.
    rows = []
    if not is_premium:
        rows.append([("\U0001F9D1 Ask about Premium", "url:" + SUPPORT_URL)])
    rows.append([("\U000000AB Back", "cb:go:menu")])
    return rows

# --- The in-game unlock -----------------------------------------------------
# Shown after tapping Unlock Premium. {cost} is PREMIUM_UNLOCK_COST and {needed}
# is what is still missing, both computed from the balance the atomic UPDATE
# actually saw - so the screen cannot quote a shortfall the database disagrees
# with. Tokens are fictional in-game credits: nothing on these screens asks for
# money, a deposit or a trading account.
MSG_PREMIUM_LOCKED = ("\U0001F451 <b>Premium Locked</b>\n\n"
                      "You need {cost} game tokens to unlock Premium.\n\n"
                      "Your balance: {balance}\n"
                      "Still needed: {needed} tokens")

def tokens_needed(balance):
    # The ONE shortfall calculation. Both the screens and the tests read it
    # from here, so a message can never quote a number the unlock disagrees
    # with. Floors at 0 so a balance above the cost never reads as negative.
    return max(PREMIUM_UNLOCK_COST - int(balance or 0), 0)

# --- Premium verification screens -------------------------------------------
# Shown while the user is in the Premium unlock flow.
#
# WORDING vs BEHAVIOUR - read before editing:
# These screens are written in real-currency terms ($, balance, account ID) by
# request. The code behind them is unchanged and still gates on game_tokens,
# a column written only by the /tokens and /tokenset admin commands, and the
# account-ID check is still format-only (5-15 digits, no panel lookup). So a
# real top-up does NOT move what this screen is actually waiting on. Keep that
# in mind before treating the copy as a description of the mechanism.
#
# The money emoji goes through pe(), which embeds the plain 💰 as the inner
# glyph: clients without premium emoji render that literal rather than nothing.
# E_MONEY is the supplied id and already existed above - not a new constant.
T_PREM_MONEY = pe(E_MONEY, "\U0001F4B0")

# {cost} is PREMIUM_UNLOCK_COST, so the figure quoted is the same one the
# unlock enforces rather than a literal typed into the copy.
MSG_PREMIUM_SHORT = (T_PREM_MONEY + " <b>Almost there.</b>\n\n"
                     "Your account is registered through our link. To unlock "
                     "access, top up your balance with <b>${cost}</b> or more "
                     "\U00002014 then send your account ID here again to "
                     "complete verification.")

# Threshold already met: the only thing left is the account-ID check.
MSG_PREMIUM_READY = (T_PREM_MONEY + " <b>Almost there.</b>\n\n"
                     "Your account is registered through our link. Your balance "
                     "meets the <b>${cost}</b> requirement \U00002014 send your "
                     "account ID here to complete verification.")

# The account ID was accepted but the threshold is still not met. Distinct from
# MSG_PREMIUM_SHORT so the user can tell the ID passed and the balance is what
# is outstanding - one screen for both would read as the ID having failed.
MSG_PREMIUM_STILL_SHORT = (T_PREM_MONEY + " <b>Almost there.</b>\n\n"
                           "Your account ID has been checked. Top up your balance "
                           "with <b>${cost}</b> or more \U00002014 then send your "
                           "account ID here again to complete verification.")

MSG_PREMIUM_UNLOCKED = ("\U0001F451 <b>Premium Unlocked!</b>\n\n"
                        "Your account ID has been verified successfully.\n\n"
                        "You now have <b>{limit} signals per day</b>.")

# The account ID did not pass the check. Says so and invites another attempt -
# there is no attempt limit and no lockout, so the user can send it as often as
# they like and each send is checked again.
MSG_ACCOUNT_ID_INVALID = ("\U00002757 <b>That account ID is not valid.</b>\n\n"
                          "An account ID is numbers only (5\U0000201315 digits). "
                          "Example: <b>123456789</b>\n\n"
                          "Send your account ID again to continue.")

# A second tap once Premium is held. The unlock statement refuses it (its WHERE
# requires is_premium = FALSE), so nothing was deducted and this only says so.
# The token balance line was dropped here: this screen now sits in a flow
# written in real-currency terms, and showing a game-token count next to it
# read as two different currencies on one screen.
MSG_PREMIUM_ALREADY = ("\U0001F451 <b>Premium is already active</b>\n\n"
                       "You have <b>{limit} signals per day</b>.")

# The unlock screens keep the plain Back button and nothing else: there is no
# purchase button because there is nothing to purchase.
UNLOCK_KB = [[("\U000000AB Back", "cb:go:menu")]]

# --- Admin level commands ---------------------------------------------------
# Replies to /premium and /startlevel. Admin-only (ADMIN_IDS); a non-admin gets
# no reply at all, so none of these strings ever reach an ordinary user.
MSG_ADMIN_USAGE   = "Usage: {cmd} <tg_id>"
MSG_ADMIN_NO_USER = "No user with tg_id {tg_id}. They must /start the bot first."
MSG_ADMIN_DONE    = "tg_id {tg_id} is now {level} \U00002014 {limit} signals/day."

# /tokens and /tokenset. Same admin-only rule as above: a non-admin gets no
# reply at all, so these strings never reach an ordinary user and the commands
# stay invisible to them.
MSG_TOKENS_USAGE  = "Usage: {cmd} <tg_id> <amount>"
MSG_TOKENS_DONE   = "tg_id {tg_id} now holds {balance} game tokens."

# Sent when a screen fails to render (see _screen_error in bot.py). Deliberately
# plain: no HTML, no custom emoji, no buttons, so it cannot fail for the same
# reason the screen did. The previous screen is left in place, so "try again"
# means tapping the button that is still on it.
MSG_SCREEN_ERROR = ("Something went wrong opening that screen. "
                    "Please try again in a moment, or send /start.")

# Shown both as the popup on a tap that is over the cap and as the screen text
# if the cap is reached while a signal is already being prepared. Plain text
# with no HTML: it is passed to cb.answer(), which does not parse markup.
MSG_DAILY_LIMIT = "Daily limit reached. Come back tomorrow."

# The limit screen still needs a way back, otherwise the user is stranded on it.
LIMIT_KB = [[("\U000000AB Back", "cb:go:menu")]]

# TODO: the picked pair only lives in memory (bot.py _pair_choice), so a restart
# mid-funnel falls back to this label.
DEFAULT_PAIR = "AUD/CAD OTC"

# Delayed follow-up sent a few seconds after the register screen opens (bot.py).
REGISTER_NUDGE = (pe(E_NUDGE_WARN, NUDGE_WARN_FALLBACK)
                  + " Only 2 Go+ activations left today."
                  "\n\nNo extensions. No second chance.\n\n"
                  + pe(E_NUDGE_ROCKET, NUDGE_ROCKET_FALLBACK)
                  + " Activate Go+ now.")

# --- Group F verification verdict messages (pe() style, factual, no scarcity) ---
_MINDEP = str(int(MIN_DEPOSIT)) if MIN_DEPOSIT == MIN_DEPOSIT.to_integral_value() else str(MIN_DEPOSIT)

# Access granted: campaign matches and deposits meet the minimum -> the "menu"
# screen above is shown instead of a text verdict.

# Campaign matches but deposits are below the minimum.
MSG_NEED_DEPOSIT = T_MONEY + " <b>Almost there.</b>\n\nYour account is registered through our link. To unlock access, top up your balance with <b>$" + _MINDEP + "</b> or more \U00002014 then send your account ID here again to complete verification."

# Account not found, or registered under a different campaign.
MSG_WRONG_LINK = pe("5447644880824181073", "\U000026A0\U0000FE0F") + " <b>Account not linked to us.</b>\n\nTo get access, your Pocket Option account must be created through our official link. Please register with the button below, then send your new account ID."

# Prepended to the menu screen when TEST_MODE is on. Blanked so the banner no
# longer shows; kept defined because bot.py still references it. NOTE: this only
# hides the notice - VERIFY_MODE=test still bypasses verification entirely.
MSG_TEST_MODE = ""

# Panel bot didn't answer in time; the retry worker will keep checking.
MSG_DELAYED = T_CLOCK + " <b>Verification is taking a moment.</b>\n\nWe're still checking your account. You'll be notified here automatically as soon as it's confirmed \U00002014 or you can send your account ID again shortly."

# Sent when a user re-sends their account ID inside UID_LOOKUP_COOLDOWN.
MSG_UID_COOLDOWN = T_CLOCK + " <b>One moment.</b>\n\nYour last check is still going through. Please wait about {seconds}s, then send your account ID again."

# Last-resort reply when the UID handler itself fails (see capture_uid). Plain
# text, no buttons, so it can't fail for the same reason the handler did.
MSG_UID_ERROR = pe("5447644880824181073", "\U000026A0\U0000FE0F") + " <b>Something went wrong on our side.</b>\n\nYour account ID wasn't processed. Please send it again in a moment \U00002014 if it keeps failing, contact support: " + SUPPORT
